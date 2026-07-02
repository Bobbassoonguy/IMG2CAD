# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A lightweight tool that converts a raster image (PNG/JPG/…) into a **DXF** file
for import into **Onshape**. DXF was chosen deliberately: Onshape imports DXF
natively into a sketch, so there is **no Onshape API, OAuth, or FeatureScript** —
the whole "get geometry into CAD" problem reduces to "write a good DXF." Do not
add an Onshape API integration unless explicitly asked; it would defeat the point.

## Commands

Setup (Windows, local venv):
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
Run the CLI / GUI (always via the venv interpreter):
```
.venv\Scripts\python img2cad.py sample.png            # convert -> sample.dxf
.venv\Scripts\python img2cad.py a.png b.png -o out    # batch -> out\*.dxf
.venv\Scripts\python img2cad.py imgs\ --format svg    # folder -> one .svg each
.venv\Scripts\python img2cad_gui.py [image]           # live-preview GUI
```
The CLI accepts multiple images / folders (batch), and `--format dxf|svg|pdf`
(also inferred from the `-o` extension). `-o` naming a directory = output folder.
There is **no test suite / linter configured.** Smoke-test with the checked-in
`sample.png` and validate output structurally with ezdxf's auditor:
```
.venv\Scripts\python -c "import ezdxf; a=ezdxf.readfile('sample.dxf').audit(); print(len(a.errors),'errors')"
```
A clean DXF must audit with **0 errors** or Onshape may reject it.

## Architecture

Two files, one shared core:

- **`img2cad.py`** — the entire pipeline and CLI, all parameterized by one
  `Options` dataclass. Stages:
  1. `load_binary()` — decode (via shared **`load_bgr()`**, alpha over white),
     grayscale, blur, then **binarize**. Otsu by default, `--adaptive`
     (`cv2.adaptiveThreshold`, for uneven light/scans), fixed `--threshold`, or
     `--canny` for outline tracing. Note the inversion convention: the *shape*
     must end up white (255); for the common dark-shape-on-light-background case
     Otsu/adaptive make the background white, so it inverts unless
     `--invert`/`opt.invert` says the foreground is light. Three image-prep hooks
     compose in here: **`opt.color`** (a picked BGR → HSV-range `color_mask()`,
     its own mode, hue compared on the circle), **`opt.crop`** (`apply_crop()`
     zeros outside an `(x0,y0,x1,y1)` ROI, keeping full dimensions so coords don't
     shift), and an optional **`region`** keep-mask arg (`apply_region()`,
     GUI-only: the masking brush ∪ **`grabcut_foreground()`**; the CLI leaves it
     None). `grabcut_foreground()` runs OpenCV GrabCut on a downscaled copy
     (coarse result, ~2–3 s on big photos) and upsamples the mask.
  2. `find_contours()` — `cv2.findContours` with **`CHAIN_APPROX_NONE`** (dense
     per-pixel boundary; the fitter needs it — a 4-point rectangle from
     `CHAIN_APPROX_SIMPLE` spuriously fits a circumscribed circle). `RETR_CCOMP`
     keeps interior holes as separate loops (a washer imports as two curves).
  3. **`_vectorize_path()` — the core "best-fit" geometry inference.** For each
     ordered path: `_depixel()` low-passes the raster staircase to recover the true
     smooth path; `_detect_corners()` splits at genuine sharp turns; `_fit_segment()`
     greedily fits the *simplest primitive within `opt.tol` px* — a straight **line**
     (`_fit_line`, chord-based), else a circular **arc** (`_fit_circle`, algebraic
     Kasa fit), else recursively splits, falling back to a **spline** only for truly
     freeform runs. A corner-free closed loop that fits a circle becomes a single
     CIRCLE. Primitives are dicts `{"kind": line|arc|circle|spline, ...}`.
     `vectorize_contour()` feeds an outline contour through it; `vectorize_centerline()`
     feeds skeleton paths (see below). The legacy `simplify_contour()` (Douglas-
     Peucker, `opt.fit=False`) still exists for one-spline-per-contour output.

     **`build_items(mask, opt)` is the single dispatch entry** used by both CLI and
     GUI: centerline → `vectorize_centerline`, else fit → `vectorize_all`, else legacy.
     After fitting it runs (when `opt.merge`, default on) an **entity-reduction
     pass** `merge_chain()` per chain — `_merge_lines` fuses collinear lines,
     `_merge_arcs` fuses co-radial adjacent arcs (and emits a real **CIRCLE** when a
     merge closes the loop), `_arcs_form_circle` collapses a full ring of arcs —
     then fillet. Fewer, cleaner entities; disable with `--no-merge`.
  3b. **Centerline mode (`opt.centerline`).** `skeleton_paths()` runs
     `skimage.morphology.skeletonize` on the mask, then walks the 1px skeleton as a
     pixel graph (nodes = endpoints/junctions with ≠2 neighbors; edges traced through
     degree-2 runs; isolated loops handled separately; sub-`prune`-length spurs
     dropped). Each traced path is vectorized like an open contour, giving a single
     medial stroke instead of a double outline. Adds a **scikit-image** dependency.
  3c. **`auto_adjust(path)`** inspects the image (border brightness → invert; image
     diagonal → tol/depixel/weld; contour-area distribution → min_area; foreground
     fraction + distance-transform stroke half-width → centerline) and returns a dict
     of suggested settings. The GUI's ✦ Auto-adjust button applies them.
  4. `write_dxf()` — ezdxf; consumes primitive-lists (fit/centerline) or point-arrays
     (legacy) and emits real **LINE / ARC / CIRCLE / SPLINE** entities. Returns a
     tally dict. It's split so the GUI can reuse the geometry: **`build_doc()`**
     builds the in-memory ezdxf doc + tally; `write_dxf` saves it; **`audit_items()`**
     builds + runs ezdxf's `.audit()` and returns `(tally, error_count)` for the GUI's
     live badge (no disk touch). **`export_file()`** dispatches by output extension to
     `write_dxf` / **`write_svg`** / **`write_pdf`** (SVG = real line/circle + sampled
     polylines, real-world sized via the unit; PDF = a minimal hand-rolled single-page
     vector doc, flattened polylines). The point transform (`make_transform`) composes **Y-flip → per-axis
     scale (`resolve_scale`, units-per-px) → rotation about the drawing center**.
     `opt.units` sets the DXF units header. When the scale is **non-uniform**
     (`sx≠sy`, i.e. aspect unlocked) circles/arcs can't stay circular, so they are
     sampled to splines; lines/splines stay exact. Optional `_emit_guides` adds a
     dashed **bounding box** (BBOX layer) and **center cross-hairs** (CENTERLINES
     layer), framed on the geometry or the image per `opt.guide_ref`.

     **`fillet_path()`** (applied in `build_items` when `opt.fillet>0`) inserts a
     tangent arc at sharp **line→line** corners: it trims both lines by
     `r/tan(α/2)` and drops in an arc through the tangent points. Only line-line
     corners are filleted (arc-involved corners are already curved); radius is auto-
     clamped to fit the shorter line.

  **Arc orientation is the subtle part.** `_arc_angles(center, p0, pm, p1)` picks
  the sweep (CCW vs CW / minor vs major) that passes through the segment midpoint
  `pm`, and returns the CCW `(start, end)` ezdxf needs. Angles are **recomputed
  from transformed points inside `write_dxf`** (not reused from image space) so the
  Y-flip's handedness change is handled correctly. If you touch arcs, re-verify by
  rendering the written DXF back (read entities → sample → compare to source).

  **Connectivity / closed shapes.** An arc is stored as just its 3 defining points
  `p0, pm, p1`; the actual circle is derived on demand via `_circle_from_3()`, so
  the arc always passes *exactly* through its (possibly welded) endpoints — no gap
  to neighbors, and endpoint dots land on the curve. `_fit_line` likewise returns
  the raw segment endpoints (chord fit), not a floating best-fit line, so adjacent
  primitives share exact junctions. `weld_endpoints()` then runs globally
  (`vectorize_all`, `opt.weld` px, union-find over a grid) to fuse near-coincident
  endpoints across the whole drawing, closing seams so Onshape sees fillable
  closed profiles. Because arcs/lines derive geometry from their endpoint fields,
  welding those fields in place is enough — no separate re-fit needed.
  **`open_endpoints(items, tol)`** is the read-only inverse: it reuses the same grid
  to return endpoints that *don't* meet any neighbor within `tol` (the gaps welding
  didn't close) so the GUI can flag them red — Onshape won't auto-join those seams.

- **`img2cad_gui.py`** — Tkinter front end that `import img2cad as core` and reuses
  the same functions (via `build_items`), so GUI and CLI never diverge. Preview is
  drawn with OpenCV, PNG-encoded in memory, shown via `tk.PhotoImage`. Layout is a
  slim **canvas toolbar** — now just **⤢ Fit to full** (`_reset_view`) and **⬚ Fit to
  area** (`_fit_to_area`, zoom to the detection-area crop) plus a hint label — above a
  zoom/pan **canvas studio**, beside a **numbered vertical step rail** sidebar.
  **Canvas tools live in the sidebar next to their settings**, not on the toolbar:
  the crop/brush/pick buttons are in ②Prepare and measure is in ④Scale, each built via
  `_tool_button(parent, mode, …)` which registers in `self._tool_btns` and toggles the
  mode (`_toggle_mode`; clicking the active tool returns to pan). **Pan is the default
  mode** (drag/scroll), there is no pan button, and **Esc cancels any tool**
  (`root.bind("<Escape>", …→_set_mode("pan"))`). `_set_mode` highlights the active
  sidebar button (`Accent.TButton` vs `TButton`), sets the cursor from `TOOLS`, and
  writes an accent toolbar hint `"<label> — <hint> · Esc to cancel"`. While any tool is
  active, `_show_source()` returns true so `_rebuild_display`/`_composed_base` show the
  **un-masked source image** — you can see and click features outside the detection
  area / mask (this is why measuring/scaling anywhere works; a scale set also flashes a
  confirmation). `_set_mode` calls `_rebuild_display` when crossing the pan/tool
  boundary so the source-image warp is ready.
  **Step-rail architecture:** the left column is a slim rail (`_build_rail`, cells =
  big numeral + word + a state dot ○/●/✓ from `_step_done`, `RAIL_STEPS`/`STEP_SUB`)
  beside a panel host; clicking a step calls `_show_step(i)` which `pack_forget`s the
  others and packs `self.panels[i]` (built once by `_panel_source/_panel_prepare/
  _panel_trace/_panel_scale/_panel_export`). Each panel is titled by `_panel()` and
  organised into `_sub()` subcategory headers with `_desc()` inline descriptions and
  hover `Tooltip`s; not-yet-built ideas render as disabled `_planned_row()`s. Steps:
  **①Source** (open/paste/`batch_folder`; project stubs) → **②Prepare** (isolate +
  threshold, with `_reveal_threshold` progressively disclosing the adaptive
  block/C sliders only in Adaptive mode; **invert lives here now**) → **③Trace**
  (Recipe/Method/Detail/Optimise/Advanced) → **④Scale** (units/size/calibrate/orient)
  → **⑤Export** (format/guides/validate/actions). Cross-cutting **View filters +
  Legend** live in a persistent footer (`_build_footer`) that shows on every step; the
  live **audit badge** + **Export** button stay pinned at the very bottom (lifted above
  the scroll canvas). The active step persists as `active_step` in prefs. Frame
  compositing is factored into **`_render_frame(cw, ch)`** (offscreen-renderable);
  `_blit` calls it and encodes. **Theming lives entirely in `theme.py`** (single
  source of truth — see `docs/THEME.md`): the GUI does `from theme import T,
  GEOMETRY_BGR as COLORS, …` and hard-codes no hexes. `theme.T` is the dark teal-on-
  jet-black semantic ramp (derived from the 5-colour brand palette), `theme.GEOMETRY_*`
  the categorical entity colours (BGR + hex), `theme.STATUS` the functional colours
  (`WARN` amber, `GAP_BGR` red, crop/hilite/measure/guides), and `theme.FONTS`/`SIZES`
  the type system — a drafting-style display face (`Bahnschrift SemiBold`, fallback
  `Segoe UI Semibold`, resolved in `_apply_theme`) for titles, `Segoe UI` for controls,
  `Consolas` for every numeric readout. New Options fields now surfaced with real
  controls: `blur`, `corner_angle`, `external_only`, `adaptive_block`, `adaptive_c`,
  `as_polyline` (wired in `_opts`; `guide_ref` still follows the Scale reference).
  **Tier-1 features:** *Paste* (Ctrl+V / button, `paste_clipboard` via `PIL.ImageGrab`
  to a temp PNG — Pillow is an optional runtime dep, guarded); *Presets* (`PRESETS`
  recipes set MODE+TUNING; any manual mode/slider change flips the combobox to "Custom"
  via the `_applying` guard); *remember-last* (`_save_prefs`/`_p` persist all
  mode/tuning/display/scale settings to `~/.img2cad_gui.json`, restored on launch, saved
  on `WM_DELETE_WINDOW`); *live audit* (`_update_audit` → `core.audit_items`, ~15 ms,
  shows `N entities · 0 audit errors ✓` or a `⚠` with error/gap counts); *gap
  highlighter* (`core.open_endpoints` → red dots, toggled by "Flag open gaps");
  *Export* (`core.export_file` picks DXF/SVG/PDF by the save-dialog extension).
  **Tier-2 features:** the canvas tools dispatch through `_on_press/_on_move/_on_release`
  by `self.mode`, mapping canvas↔image via the stored display transform
  (`_canvas_to_img` inverts `_disp_R`/`_disp_tv`; `_img_to_canvas` for overlays):
  *Set detection area* (mode key `"crop"`; drag a box → `opt.crop`, tiny drag clears it) — a detection-only limit that never distorts the preview); *Brush* (**left**-drag
  paints `self.paint_mask`, an erase-region via `_region()`; **right**-drag paints
  `self.add_mask`, force-foreground pixels unioned in by `load_binary`'s `add` arg); *Pick* (eyedropper
  → `self.pick_color`/`color_active`, HSV `Color range` slider); *Isolate subject*
  (one-shot `core.grabcut_foreground` → `self.gc_mask`); *Measure* / click-to-scale
  (click two ends → a themed `LengthDialog` — number entry + a unit dropdown over
  `MEASURE_UNITS`, input unit independent of the output unit — giving a real length → `core.solve_scale_1line` uniform / a 2nd
  ⟂ line → `solve_scale_2line` unlocks aspect, both feeding `tw_mm`/`th_mm`). Prep
  masks (detection area / brush± / GrabCut) fold into the mask-cache key via a
  `_region_ver` counter so slider drags still skip re-decode. Right-drag pans in
  every other mode; **`_scale_ratio` returns 1.0 whenever aspect is locked**, so the
  preview only stretches from an explicit unlock (a 2-line measure) — changing the
  detection area (which shifts the geometry bounds) can never distort the display. The **Threshold** panel (Auto/Manual/
  Adaptive combo + a live `cv2.calcHist` **histogram** you drag to set a manual
  cutoff) steers binarization — but it **disables itself with a reason**
  (`_threshold_active`/`_sync_threshold_ui`) whenever Canny or color isolation is
  active, since `load_binary` bypasses the threshold path in those modes (a common
  "why does the threshold do nothing?" trap). **Display** adds a **background view** (dimmed mask ↔
  original image — both warped by the same display transform in `_rebuild_display`,
  composited in `_composed_base` and cached) and a **highlight-detected-pixels**
  overlay (washes the warped binary mask in teal so you see exactly what feeds the
  fitter). *Merge similar entities* toggles `opt.merge`.
  **Window sizing:** `main()` clamps height to the screen (`sh-80`) so the pinned
  Export button is always reachable and the sidebar scrolls above it.
  **Scroll-canvas gotcha:** the sidebar is a frame embedded in a `tk.Canvas`; when it's
  taller than the viewport, Windows Tk does *not* clip the embedded frame, so its
  overflow would stack above and white-out the pinned Export area — the pinned frame is
  therefore `.lift()`ed above the canvas and abuts it with no exposed parent padding.
  (A large white block below the on-screen fold in a `screenshot_app.ps1` grab is just
  PrintWindow failing to render off-screen native widgets — not a real bug; size the
  window to fit the screen to verify the lower sidebar.)
  Notes: slider drags are **debounced** (`_schedule`, 70 ms) so centerline recompute
  stays smooth. Geometry is computed in image space (`draw_img`) then transformed
  into a display space (`_rebuild_display` → `dbase`/`ddraw`/`dw`/`dh`) applying both
  rotation and the non-uniform aspect stretch (`_scale_ratio`), so the **preview is
  WYSIWYG**; zoom/pan operate on that display space. Any control that changes rotation
  *or* aspect must call `_rebuild_display` before `_blit` (a plain `_blit` won't show a
  new stretch — it's baked into `dbase`/`ddraw`). **Scale model:** the canonical value
  is the target OUTPUT size in mm (`tw_mm`/`th_mm`, object-axis / pre-rotation extent
  of the chosen reference), not mm/px — so it stays fixed when a tuning slider changes
  the geometry extent; `_opts` derives `scale_x/scale_y` as `target/reference_px`. The
  binarized mask is cached (`_mask_cache`, keyed on `path,invert,canny,blur,threshold`)
  so slider drags don't re-decode the image. The Rotate spinbox
  holds **clockwise** degrees; preview uses cv2 angle `-deg` and export uses
  `opt.rotate = -deg` (its transform is CCW in y-up), so both turn the same way —
  verified by rendering preview vs DXF. Mouse-wheel scrolls the sidebar via
  `_bind_wheel` (recursively bound per-widget, not `bind_all`, so it never fights the
  image canvas's zoom wheel).
  Tkinter is the only hard runtime dep; **Pillow** is now used by the app too, but
  *only* for clipboard paste (imported lazily, with a friendly install prompt if
  absent) — everything else still runs without it.

- **`*.bat`** — Windows convenience launchers (drag-drop convert, GUI). They prefer
  `.venv\Scripts\python[w]` and fall back to system `python[w]`.

- **`packaging/`** — turns the tool into a standalone Windows app. `make_icon.py`
  generates `img2cad.ico` (PIL); `build.ps1` runs PyInstaller (windowed, onedir,
  bundles skimage/ezdxf data) → `dist\img2cad\img2cad.exe`; `install.ps1` /
  `uninstall.ps1` do a per-user (HKCU, no admin) install: copy to
  `%LOCALAPPDATA%\Programs\img2cad`, Start-Menu/Desktop shortcuts, and register
  "Open with img2cad" for image extensions. The GUI's `main()` sets an explicit
  AppUserModelID (`img2cad.app`) and window icon via `_icon_path()` (resolves both
  from source and from a PyInstaller `sys._MEIPASS` bundle). The app already accepts
  an image path as `argv[1]`, which is what the file association passes.

## Conventions that matter

- **Keep it one-file-core + stdlib GUI.** The project's value is being scrappy and
  lightweight. Prefer OpenCV/NumPy/SciPy/ezdxf primitives over new dependencies.
- Anything the GUI needs must live as a reusable function in `img2cad.py` taking an
  `Options`, not be reimplemented in the GUI.
- Simplification tolerances are expressed as fractions of perimeter, never raw
  pixel counts, so behavior is consistent across image sizes.
