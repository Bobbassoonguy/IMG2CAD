# img2cad — 20 Ideas to Make Image → DXF → CAD 100× Easier

Ranked, scored ideas spanning quick fixes → re-architectures, all aimed at one
goal: **fewer steps and less fiddling between "I have a picture" and "I'm
extruding clean geometry in Onshape."**

Each idea is scored:

- **Complexity** 1–5 — build effort (1 = an afternoon; 5 = a real project).
- **QoL** 1–5 — how much it improves the day-to-day experience (5 = transformative).
- **Bang** = rough value-for-effort (QoL-weighted vs. complexity). ★ marks the
  quadrant of *high QoL, low complexity* — do these first.

Scores reflect *this* codebase (one-file core + Tkinter GUI, OpenCV/NumPy/SciPy/
ezdxf) and the [Onshape research](#appendix-onshape-findings) at the end, which
validated the whole DXF-native premise and surfaced several concrete wins.

---

## Master table (sorted by bang-for-buck)

| # | Idea | Cmplx | QoL | Bang | Tier |
|---|------|:---:|:---:|:---:|------|
| 1 | Paste image from clipboard (Ctrl+V) | 2 | 5 | ★★★ | Quick |
| 7 | In-GUI crop / region-of-interest box | 3 | 5 | ★★★ | Medium |
| 14 | Axis & angle snapping (constraint-friendly output) | 3 | 5 | ★★★ | Geometry |
| 8 | Masking brush + 1-click background removal (GrabCut) | 3 | 5 | ★★★ | Medium |
| 3 | Live DXF audit + entity-count badge in GUI | 2 | 4 | ★★★ | Quick |
| 5 | Open-profile / gap highlighter | 2 | 4 | ★★★ | Quick |
| 4 | Settings presets + remember last settings | 2 | 4 | ★★★ | Quick |
| 10 | Adaptive/local thresholding for uneven light | 2 | 4 | ★★★ | Medium |
| 12 | Entity-reduction / primitive-merge pass | 3 | 4 | ★★ | Medium |
| 2 | Batch / folder convert | 1 | 3 | ★★ | Quick |
| 6 | SVG + PDF export alongside DXF | 2 | 3 | ★★ | Quick |
| 9 | Color-pick isolation (eyedropper) | 3 | 4 | ★★ | Medium |
| 11 | Interactive threshold + histogram + live mask | 3 | 4 | ★★ | Medium |
| 13 | Tangent-continuity (G1) cleanup | 4 | 4 | ★★ | Geometry |
| 17 | Image-type classifier → full recipe auto-pick | 3 | 4 | ★★ | Bigger |
| 15 | Smarter auto-fillet (per-corner curvature radius) | 4 | 4 | ★★ | Geometry |
| 16 | Symmetry detection & enforcement | 4 | 4 | ★★ | Geometry |
| 19 | Project files (.img2cad): save image + settings | 2 | 3 | ★★ | Bigger |
| 18 | Intelligent-scissors assisted tracing | 5 | 4 | ★ | Bigger |
| 20 | FeatureScript emitter (API-free Onshape push) | 5 | 3 | ★ | Bigger |

**Recommended first five (best effort-to-payoff):** #1 clipboard paste, #7 crop
box, #3 audit badge, #5 gap highlighter, #14 axis/angle snapping. Together they
kill the most common friction points — the round-trip to save a file, isolating a
subject, and the "why won't it extrude / why is my sketch unconstrainable" surprises
in Onshape.

---

## Tier 1 — Quick wins

### 1. Paste image from clipboard (Ctrl+V) ★ · Cmplx 2 · QoL 5
Let the GUI accept an image straight from the clipboard — snip a region of your
screen, or copy an image from a browser/PDF, and paste it in. Eliminates the
"save a PNG to disk, then open it" round-trip, which is the single most common
friction point. Pair with drag-and-drop onto the window. (Tkinter can read
`CF_DIB`/PNG from the clipboard via a small Win32 or Pillow `ImageGrab` hook.)

### 2. Batch / folder convert · Cmplx 1 · QoL 3
`img2cad.py *.png` or point at a folder → one DXF per image with the same
settings; drag multiple files onto `convert-drop.bat`. Trivial loop over the
existing pipeline; big time-saver for sets of parts/logos.

### 3. Live DXF audit + entity-count badge ★ · Cmplx 2 · QoL 4
Show `175 entities · 0 audit errors ✓ · well under Onshape's ~8000 limit` right in
the status bar, updated on each recompute. The research confirms Onshape **rejects
DXFs that fail structurally** and **chokes past ~8000 entities** — surface both
*before* the user exports and switches apps. Reuses `ezdxf …audit()` and the
existing tally dict.

### 4. Settings presets + remember last settings ★ · Cmplx 2 · QoL 4
Named recipes ("Logo", "Gasket", "Line-art", "Scanned sketch") in a dropdown, plus
auto-restore of the last-used settings on launch. Stops the re-dial-every-time
tax. Just serialize the `Options` dataclass to a small JSON in `%APPDATA%`.

### 5. Open-profile / gap highlighter ★ · Cmplx 2 · QoL 4
Draw un-welded endpoints (primitives whose end doesn't meet a neighbor within
`weld`) as **red dots** in the preview. The research is explicit: Onshape **does
not auto-join coincident endpoints**, so an open seam = "can't extrude" later.
Seeing the gap *before* export (and knowing to bump *Weld gaps*) removes a classic
dead-end. Data is already computed inside `weld_endpoints()`.

### 6. SVG + PDF export alongside DXF · Cmplx 2 · QoL 3
The primitive list already knows lines/arcs/circles/splines — emit SVG (and PDF)
too. Opens up laser cutters, other CAD, and quick visual sharing with zero new
geometry work.

---

## Tier 2 — Medium features (the big usability levers)

### 7. In-GUI crop / region-of-interest box ★ · Cmplx 3 · QoL 5
Drag a rectangle on the loaded image to restrict tracing to that region. This is
*the* answer to "isolate one outline in a busy image" (see WORKFLOW.md §5) without
leaving the app. Add a lasso later for non-rectangular regions. Enormous payoff for
real-world photos and multi-element art.

### 8. Masking brush + one-click background removal ★ · Cmplx 3 · QoL 5
(a) A paint brush to wipe clutter to background color; (b) a **"Isolate subject"**
button using **OpenCV GrabCut** (already a dependency — no new package) to auto-cut
the main object from its background. This automates the #1 image-doctoring step
(WORKFLOW.md §4.1) that currently forces a detour into Photoshop/GIMP.

### 9. Color-pick isolation (eyedropper) · Cmplx 3 · QoL 4
Click a color in the image to trace only regions of that color (HSV range mask).
Perfect when the target element is a distinct color in a busy composition.

### 10. Adaptive / local thresholding for uneven light ★ · Cmplx 2 · QoL 4
Add `cv2.adaptiveThreshold` as an alternative to global Otsu. Otsu needs one global
cutoff and fails on photos/scans with gradients or shadows — adaptive thresholding
handles that whole class of hard images that today require external doctoring.

### 11. Interactive threshold + histogram + live mask · Cmplx 3 · QoL 4
Show the grayscale histogram with a draggable threshold line and a live black/white
mask preview, so the user *sees* the binarization they're steering instead of
guessing at a number. Demystifies the most important (and most opaque) stage.

### 12. Entity-reduction / primitive-merge pass · Cmplx 3 · QoL 4
A post-fit cleanup: merge consecutive **collinear lines** into one, fuse
**co-radial adjacent arcs**, and collapse a full ring of arcs into a single
**CIRCLE**. Fewer, cleaner entities = a tidier Onshape sketch and safe distance from
the ~8000-entity wall the research flagged. Complements the existing fitter.

---

## Tier 3 — Smart DXF geometry optimization (the tangent/fillet bonus)

> These target the "make the CAD *nice*, not just present" gap — clean tangencies,
> constrainable sketches, real fillets, true symmetry.

### 13. Tangent-continuity (G1) cleanup · Cmplx 4 · QoL 4
Where a **line meets an arc** or **arc meets arc**, nudge the shared joint so the
tangent directions actually match (G1 continuity) rather than kinking by a degree
or two from pixel noise. Onshape then reads a smooth transition, tangent
constraints hold, and downstream fillets/offsets behave. Builds directly on the
endpoint-derived arc/line representation described in CLAUDE.md.

### 14. Axis & angle snapping (constraint-friendly output) ★ · Cmplx 3 · QoL 5
Snap lines within a few degrees of horizontal/vertical to **exactly** H/V; snap
near-equal radii to equal; near-collinear runs to collinear; near-90° corners to
square. The result is a sketch that's **trivially constrainable/dimensionable** in
Onshape instead of a cloud of "almost straight" lines you have to fight. This is
one of the highest QoL-per-effort geometry wins — a mechanical part suddenly
behaves like it was drawn on purpose.

### 15. Smarter auto-fillet (per-corner curvature radius) · Cmplx 4 · QoL 4
Today `--fillet` applies one global radius to sharp line-line corners. Upgrade it
to (a) **estimate each corner's radius from local curvature** so originally-rounded
corners that got squared off are restored at their true radius, and (b) detect
which corners *should* stay sharp. Turns filleting from a blunt instrument into
faithful reconstruction.

### 16. Symmetry detection & enforcement · Cmplx 4 · QoL 4
Detect mirror (and simple rotational) symmetry, then **average the halves** so a
symmetric part comes out perfectly symmetric instead of subtly lopsided from pixel
noise. Optional "enforce symmetry" toggle. Big credibility win for brackets,
gaskets, and logos.

---

## Tier 4 — Bigger bets / re-architectures

### 17. Image-type classifier → full recipe auto-pick · Cmplx 3 · QoL 4
Upgrade **Auto-adjust** from per-setting heuristics to first **classifying the
image** (flat-color logo / line-art / photo / scanned sketch / mechanical part) and
then applying the whole matching recipe from WORKFLOW.md §3 — mode, thresholding,
tol, weld, fillet — in one shot. Cheap features (color count, edge density,
foreground fraction, stroke-width stats) already exist in `auto_adjust`.

### 18. Intelligent-scissors / live-wire assisted tracing · Cmplx 5 · QoL 4
Click a few points along a boundary and have the path **snap to the strongest edge**
between them (classic live-wire / intelligent scissors). The most robust way to pull
one clean outline out of a genuinely messy image where thresholding can't. A real
project, but a headline feature.

### 19. Project files (.img2cad) — save image + settings · Cmplx 2 · QoL 3
Save the image reference + all `Options` + crop/mask as a small project file you
can reopen, tweak, and re-export. Makes iteration and "redo it slightly bigger"
painless, and pairs naturally with presets (#4).

### 20. FeatureScript emitter — API-free Onshape push · Cmplx 5 · QoL 3
A strategic alternative to DXF that **stays true to the "no Onshape API/OAuth"
ethos**: emit an Onshape **custom feature** (FeatureScript) with the geometry baked
in as array literals (`newSketchOnPlane` → `skLineSegment`/`skArc` → `skSolve`). The
research confirms this needs **no REST API, no OAuth** — the user just adds the
custom feature — and it **sidesteps DXF's fragile spline import** entirely by
constructing native sketch entities. Niche and involved, but the only path that
could beat the DXF handoff on robustness. (Keep DXF as the default; this is an
opt-in export target.)

---

## Honorable mentions (deliberately not in the 20)

- **Direct REST-API push into Onshape** — technically possible (upload+translate, or
  a `BTMSketch` feature POST) but requires **OAuth2 + burns the annual API quota**
  (Free/EDU: 2,500 calls/yr). Explicitly against `CLAUDE.md`'s ethos; #20 gets the
  same "no manual import" benefit without the API. Listed only for completeness.
- **Browser/WASM version** (OpenCV.js) — zero-install web tool; large re-architecture,
  worth it only if you want to distribute widely.
- **Potrace-quality silhouette fitter** — swap in a best-in-class curve fitter for
  freeform silhouettes; overlaps with the existing fitter's job, so lower marginal
  value.

---

## Already correct (validated by the research — no action needed)

- **`$INSUNITS` header is set** (`doc.units`, img2cad.py:725) — the research called
  this out as the #1 scaling gotcha (geometry landing 25.4× off). Already handled.
- **Bias toward LINE/ARC/CIRCLE over splines** — matches the finding that SPLINE is
  the single riskiest entity for Onshape's importer. Keep it.
- **`weld_endpoints()`** — justified: Onshape does *not* auto-join coincident
  endpoints, so shared exact coordinates are necessary for fillable closed profiles.
- **Low entity count from primitive fitting** — exactly where naive auto-tracers
  (Inkscape/Illustrator) fail by blowing past Onshape's ~8000-entity limit.

---

## Appendix: Onshape findings

Condensed from a multi-source, cited research pass (full sources in the research
transcript). The takeaway: **Onshape has no native raster→vector auto-trace, and a
clean, low-count DXF of true primitives is exactly the gap the ecosystem leaves
open — img2cad's core premise is validated.**

- **DXF import = real editable sketch entities** via the *Insert DXF/DWG* sketch
  tool (start a sketch first, insert into an empty one). LINE/ARC/CIRCLE import
  reliably; **SPLINE is the worst-supported** (discontinuous/closed splines often
  fail); LWPOLYLINE gets exploded into segments; TEXT/HATCH/ELLIPSE aren't
  dependable sketch geometry.
- **Units:** DXF is effectively unitless; `$INSUNITS` drives interpretation
  (1=inch, 4=mm). The first dimension a user applies auto-scales the whole sketch.
- **Coincident endpoints are not auto-joined** — welding to exact shared coords is
  necessary (and occasionally still needs a Use/Project step).
- **~8000-entity practical import ceiling** (community-reported) — keep counts low.
- **Native "Insert image"** exists as a *visual underlay to trace over manually* —
  **no auto-trace**. The community "Image" FeatureScript is a backdrop/reference
  tool only; it does not vectorize.
- **Extension mechanisms:** (a) OAuth-connected integrated apps, (b) **FeatureScript
  custom features — no OAuth, can create sketch geometry programmatically**, (c)
  connected apps via REST. No image-tracing App Store app was found.
- **FeatureScript** can auto-fillet (`opFillet`) and build sketches
  (`newSketchOnPlane` + `skLineSegment`/`skArc`), but has **no file/network I/O** —
  geometry must be embedded as literals (basis for idea #20).
- **REST API** could upload+translate a DXF or POST a `BTMSketch` feature, but needs
  OAuth2/API keys and has annual call quotas — the DXF-file handoff avoids all of it.
