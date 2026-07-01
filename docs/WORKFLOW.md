# img2cad — Image → CAD Workflow Guide

How to reliably get from *a picture* to *clean, editable CAD geometry* in Onshape,
plus the two skills that make or break the result: **doctoring an image so it
traces cleanly**, and **telling the tool which outline you actually want** inside a
busy picture.

This is a practical field guide, not API docs. For flags, run
`.venv\Scripts\python img2cad.py -h`; for architecture see `CLAUDE.md`.

---

## 1. The core pipeline (what actually happens)

```
image  ──►  binarize      ──►  find contours  ──►  vectorize        ──►  DXF
(png/jpg)   (shape = white)    (outer + holes)     (line/arc/circle/     (LINE/ARC/
            Otsu or Canny                           spline, best-fit)     CIRCLE/SPLINE)
```

Everything downstream depends on the **binarization** step producing a clean
black-and-white mask where **the shape you want is one solid white region**. If
that mask is right, the geometry is almost always good. If it's wrong, no slider
will save you — you fix it upstream, in the image. That single fact drives this
whole guide.

Three things you're always deciding:

| Decision | Question | Controls |
|---|---|---|
| **What's foreground?** | Is my shape the dark part or the light part? | `--invert` / *Invert* |
| **Fill or outline?** | Do I want the filled silhouette, or just traced edges? | `--canny` / *Trace outlines*, and outline-vs-`--centerline` |
| **How simple?** | Few clean curves, or faithful to every wobble? | `--tol`, `--depixel`, `--corner-angle` |

---

## 2. The end-to-end workflow

1. **Pick the mode that matches your goal** (§3 scenarios).
2. **Open in the GUI** (`img2cad-gui.bat`) and hit **✦ Auto-adjust** first — it
   inspects the image (border brightness, size, speck distribution, stroke width)
   and sets invert / tol / weld / centerline for you. Start from its guess.
3. **Read the preview like an X-ray.** Blue = spline, and lines/arcs/circles in
   their legend colors. Endpoint dots show where primitives join. You are looking
   for: (a) the right region detected, (b) straight things straight, (c) round
   things as arcs/circles not splines, (d) no gaps, (e) no speck confetti.
4. **Fix in this order** (cheapest lever first):
   - Wrong region / inverted → *Invert*, or **doctor the image** (§4–5).
   - Too many tiny pieces / blocky → raise **Simplify** (`--tol`).
   - Jagged where it should be smooth → raise **De-jag** (`--depixel`).
   - Speck confetti → raise **Ignore specks** (`--min-area`) or **blur**.
   - Open profile / gaps → raise **Weld gaps** (`--weld`).
   - Sharp corners you want rounded → **Fillet corners** (`--fillet`).
5. **Set real-world size.** Enter **Width/Height in mm/in** (the canonical output
   size) and **Units**. Lock aspect unless you deliberately want a stretch.
6. **Save DXF.**
7. **In Onshape:** new sketch (or right-click a plane) → **Import DXF/DWG** → pick
   the file → it lands as editable sketch entities → extrude / fillet / offset.
8. **Sanity-check the DXF** structurally before trusting it:
   ```
   .venv\Scripts\python -c "import ezdxf; a=ezdxf.readfile('out.dxf').audit(); print(len(a.errors),'errors')"
   ```
   A clean DXF audits with **0 errors**; otherwise Onshape may reject it.

---

## 3. Scenarios (match the picture to the recipe)

Each scenario = a *kind of source image* and the settings that get you there fastest.

### A. Clean vector-style logo / silhouette (solid shapes, flat colors)
The easy case. Filled-region mode, default fit.
```
img2cad.py logo.png
```
- Round parts should come in as **arcs/circles** (filletable), straight parts as
  **lines**. If a circle comes in as a spline, you likely fed it a 4-point
  approximation — keep the default dense contour tracing.
- Letters/rings import as **double loops** (outer + inner). That's correct for a
  filled silhouette. If you want single strokes to thicken in CAD, use **centerline**.

### B. Line art / single-weight strokes you'll thicken or offset in CAD
A logo made of strokes, a monogram, a wiring/pipe path, a signature.
```
img2cad.py art.png --centerline
```
- Skeletonizes each stroke to **one medial path** instead of tracing both edges.
- Tune **De-jag** and prune (min spur length) if you get whiskers at junctions.
- This is usually what you want when the drawing *represents a path*, not an area.

### C. Mechanical part / gasket / bracket outline (needs true geometry)
You want lines truly straight, holes truly round, corners crisp — for extrude+fillet.
```
img2cad.py part.png --tol 1.5 --weld 2 --fillet 3
```
- Lower **tol** = more faithful; **weld** closes seams into a fillable closed
  profile; **fillet** rounds sharp line→line corners with a tangent arc.
- Interior holes (bolt circles) are kept as separate loops automatically
  (`RETR_CCOMP`), so a washer imports as two curves.
- Consider `--units in --scale <in/px>` or set exact Width to hit real dimensions.

### D. Photograph of an object (uneven light, background clutter)
The hard case. **Doctor the image first** (§4) — threshold a photo and you get
noise. Two paths:
- **Silhouette:** mask the object to solid black on white, then filled mode.
- **Outline only:** `--canny` traces edges, but on a raw photo it traces *every*
  edge (texture, shadows). Almost always better to clean the image first.
```
img2cad.py photo.png --canny --blur 5 --min-area 80
```

### E. Scanned hand drawing / pencil sketch
```
img2cad.py sketch.png --depixel 2.0 --min-area 60 --blur 5
```
- Raise **De-jag** hard to recover smooth intent from the pencil wobble and scan
  grain; raise **Ignore specks** to kill paper texture. Often pairs with
  centerline if the sketch is line-based.

### F. Busy / detailed image where you want *one* element
Cropping and masking beat every slider here — see §5.

### G. Something with holes (washer, ring, stencil, letter counters)
Default handling keeps holes. Use `--external-only` **only** when you want the
solid outer silhouette and want to ignore the counters/holes.

---

## 4. Doctoring an image so it traces cleanly

The tool is only as good as the black-and-white mask. Time spent in an image
editor (Photoshop, GIMP, Krita, Affinity, Photopea, even Paint 3D / paint.net)
usually beats an hour of slider-wrangling. The goal of doctoring is a single,
**high-contrast, solid** shape on a **uniform** background.

**The target you're aiming for:** solid **black shape on pure white** (or vice
versa), hard edges, no gradients, no interior gaps, no stray marks.

Techniques, roughly in order of usefulness:

1. **Remove/flatten the background.** Erase or select-and-delete everything that
   isn't your shape; fill the background one flat color. A busy or gradient
   background is the #1 cause of garbage output. (Modern "remove background" /
   subject-select tools do this in one click.)
2. **Maximize contrast → threshold.** Boost contrast, or use the editor's
   Threshold / "posterize to 2 levels" to force a clean bilevel image. Do this in
   the editor when the tool's Otsu can't find one global cutoff (uneven lighting).
3. **Fill the shape solid.** If your shape is an *outline drawing* but you want the
   *filled silhouette*, flood-fill the interior. Conversely, if it has unwanted
   fills, clear them. Decide: do I want the area, or the lines?
4. **Close gaps in outlines.** A broken outline won't enclose a region. Draw the
   missing pixels (a 1–2px black stroke) so the boundary is continuous — or lean on
   `--weld` for small gaps, but fixing the pixels is more reliable for big ones.
5. **Clean speckles and JPEG mush.** Despeckle / median-blur, or paint out dust.
   JPEG artifacts around edges trace as wobble — a light blur before threshold
   helps, or re-export the cleaned image as **PNG**.
6. **Simplify deliberately.** Blur then threshold to *round off* tiny features you
   don't want as separate curves. This is a design choice, not just cleanup.
7. **Straighten / de-skew.** If the part is rotated in the photo, rotate it upright
   in the editor (or use `--rotate` on export) so axes line up in CAD.
8. **Up-res thin features.** If strokes are ~1px, scale the image up 2–3× before
   tracing so corner detection and centerline have pixels to work with.
9. **Boost the alpha edge.** For PNGs with transparency, the tool composites alpha
   over white — make sure the alpha is crisp (not a soft feather) or you'll get a
   fuzzy silhouette edge.

**Rule of thumb:** if *you* can't instantly see the single clean shape in the
black-and-white version, neither can the tracer. Fix that first.

---

## 5. Isolating *one* outline inside a busy image

When the picture has many elements and you want just one (one gear in an assembly
photo, one letter in a wordmark, one panel in a diagram), don't fight it with
detection flags — **tell the tool where to look** by reducing the image to that
element. Cheapest methods first:

1. **Crop to it.** The fastest isolation. Crop tight so only the target (plus a
   little margin) remains. Half the "busy image" problem disappears here.
2. **Paint out everything else.** Flood the surrounding elements with the
   background color so only your target survives thresholding. A few strokes with a
   fat brush is often faster than precise selection.
3. **Mask by selection.** Use the editor's magic-wand / lasso / subject-select to
   isolate the element, then put it alone on a white canvas. This is the reliable
   method when the target touches or overlaps its neighbors.
4. **Exploit color.** If your target is a distinct color, use *Select by Color* to
   isolate it, or desaturate everything else. Then threshold the isolated layer.
5. **Let size filter for you.** If the clutter is smaller than the target, raise
   **Ignore specks** (`--min-area`) so small contours are dropped and only the big
   one survives. (Works only when the target is clearly the largest region.)
6. **Outer-only when you want the silhouette.** If the busyness is *interior*
   detail and you only need the outer boundary, `--external-only` ignores all inner
   loops. If you want to keep counters/holes, leave it off.
7. **Fill vs. edges to your advantage.** A busy interior traces as dozens of curves
   in filled mode. If the element is defined by its *silhouette*, fill it solid so
   the interior detail collapses into one region.
8. **Trace outlines, not fills, for line diagrams.** For schematic/CAD-like line
   images, `--canny` follows the drawn strokes; combine with cropping so it only
   sees the strokes you care about.
9. **Divide and conquer.** For a multi-part design, isolate and export each element
   to its own DXF, then place them together on planes/sketches in Onshape. Cleaner
   than one over-stuffed sketch.

**Workflow that almost always works on a busy image:** crop → remove background →
paint out neighbors → threshold to solid black-on-white → filled mode with a
generous `--min-area`. Roughly 90 seconds, and it beats every detection-flag combo.

---

## 6. Quick troubleshooting map

| Symptom | Most likely fix |
|---|---|
| Nothing detected / inverted colors | `--invert` (foreground is the light region) |
| Confetti of tiny curves | ↑ `--min-area` (*Ignore specks*), or `--blur 5` |
| Blocky / too many segments | ↑ `--tol` (*Simplify*) |
| Jagged where it should be smooth | ↑ `--depixel` (*De-jag*) |
| Round hole imports as a wavy spline | Keep default dense tracing; lower `--tol` a touch |
| Open profile / won't extrude in Onshape | ↑ `--weld`; or close the gap in the image (§4.4) |
| Sharp corners, want rounds | `--fillet <radius>` |
| Traces both edges of a stroke, want one | `--centerline` |
| Interior holes missing / unwanted | drop / add `--external-only` |
| Wrong size in Onshape | set Width/Height + Units (or `--scale` / `--width-mm`) |
| Onshape rejects the DXF | run the ezdxf audit (§2.8); expect 0 errors |

---

## 7. Golden rules

- **Fix the image, not the sliders,** when the black-and-white mask is wrong.
- **Crop and mask** to isolate an element; don't ask detection to guess.
- **Match the mode to intent:** area → filled outline; path → centerline; edges →
  canny; true geometry for CAD → default fit with low `--tol` + weld + fillet.
- **Always audit the DXF** (0 errors) before importing.
- **Auto-adjust first,** then nudge. It gets you 80% there on most images.
