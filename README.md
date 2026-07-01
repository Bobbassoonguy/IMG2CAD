# img2cad

Turn a PNG/graphic into a clean **DXF** you can import straight into **Onshape**,
then extrude and fillet. Edges are auto-simplified into as few, clean curves as
possible so CAD work is easy.

No Onshape API or login needed — Onshape imports DXF natively into a sketch.

## Quick start (Windows)

1. One-time setup (creates a local Python env with the libraries):
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
2. **Easiest:** double-click **`img2cad-gui.bat`** → Open image → click
   **✦ Auto-adjust** (it inspects the image and sets everything for you) → glance
   at the preview → **Save DXF**. Fine-tune with the sidebar sliders if needed.
   Or drag a PNG onto **`convert-drop.bat`** to make a `.dxf` beside it instantly.
3. In Onshape: start a sketch (or right-click a plane) → **Import DXF/DWG** →
   pick the file → extrude / fillet as normal.

## Command line

```
.venv\Scripts\python img2cad.py picture.png                 # -> picture.dxf (lines+arcs+splines)
.venv\Scripts\python img2cad.py logo.png --tol 3            # simpler geometry (coarser fit)
.venv\Scripts\python img2cad.py scan.png --depixel 2.0      # de-jag a rough/pixelated edge more
.venv\Scripts\python img2cad.py art.png --width-mm 120      # scale to 120 mm wide
.venv\Scripts\python img2cad.py part.png --fillet 5         # round sharp corners (radius px)
.venv\Scripts\python img2cad.py plan.png --units in --scale 0.02 --rotate 90
.venv\Scripts\python img2cad.py logo.png --export-bbox --export-centerlines
.venv\Scripts\python img2cad.py photo.jpg --canny           # trace outlines, not fills
.venv\Scripts\python img2cad.py logo.png --centerline       # single medial path per stroke
.venv\Scripts\python img2cad.py part.png --no-fit           # one plain spline per contour
```

**Outline vs Centerline:** outline mode traces both edges of every stroke (a
letter comes in as a double loop). **Centerline** mode skeletonizes each stroke to
a single editable path — usually what you want for line-art / logos you'll thicken
or offset in CAD. In the GUI, *Auto-adjust* picks the right one for you.

Run `python img2cad.py -h` for all options.

### Getting the cleanest result
- By default it **infers a mix of straight lines, circular arcs, and splines** —
  straight edges become real lines, round parts become real arcs (great for
  filleting), and only genuinely freeform runs become splines.
- **Geometry too blocky / too many tiny pieces?** raise `--tol` (GUI: *Simplify*).
- **Jagged where it should be smooth/straight?** raise `--depixel` (GUI: *De-jag*) —
  this recovers the true edge from the pixel staircase.
- **Gaps between segments / open profile in CAD?** endpoints are auto-**welded**
  (GUI: *Weld gaps*, or `--weld <px>`) so adjacent lines/arcs share exact points and
  shapes close up. Raise `--weld` to bridge bigger gaps, `--weld 0` to disable.
- **Nothing detected / inverted?** add `--invert` (foreground is the light region).
- **Speckly?** raise `--min-area` (GUI: *Ignore specks*), or `--blur 5`.

## Install as a Windows app (taskbar + right-click "Open with")

Build a standalone `img2cad.exe`, then install it for your user (no admin):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
powershell -ExecutionPolicy Bypass -File packaging\install.ps1
```

This adds Start Menu + Desktop shortcuts (pin either to the taskbar) and registers
**Open with → img2cad** for common image types. See `packaging/README.md` for details
and uninstall.

## How it works
`image → binarize (Otsu / Canny) → findContours → de-pixelate → split at corners →
fit line / arc / spline (best fit ≤ tol) → DXF (LINE / ARC / CIRCLE / SPLINE)`.
Built on OpenCV, NumPy, SciPy, and ezdxf.
