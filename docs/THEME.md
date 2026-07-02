# img2cad — Theme

All colour and type for the GUI live in one place: **`theme.py`** at the repo root.
`img2cad_gui.py` imports from it and defines no colours of its own. To re-skin the app,
edit `theme.py` only — nothing in the GUI hard-codes a hex value.

## Design brief

img2cad turns a picture into a machinable DXF, so the look is a **drafting table at
night**: a graded teal system on a jet-black ground, with traced geometry glowing on
the dark canvas. Type pairs a **technical DIN-style display face** (titles) with a
**monospace numeral face** (every readout) so measurements feel like a caliper, not a
web form.

## Brand palette

The five client swatches, used verbatim as the root of everything (`theme.PALETTE`):

| Swatch | Hex | Name | Role in the UI |
|---|---|---|---|
| ⬛ | `#1F363D` | Jet Black | window/rail ground (darkened for surfaces) |
| 🟦 | `#40798C` | Cerulean | secondary accent — measure lines, links |
| 🟩 | `#70A9A1` | Tropical Teal | **primary accent** — active step, buttons, focus |
| 🟢 | `#9EC1A3` | Muted Teal | secondary ink — labels, hints, histogram |
| 🟨 | `#CFE0C3` | Tea Green | primary ink (lightened toward white) |

## Semantic ramp (`theme.T`)

Widgets reference **roles**, never raw colours, so a re-theme never touches the GUI.

| Role | Hex | Purpose |
|---|---|---|
| `canvas` | `#13232A` | deepest surface, behind the image |
| `bg` | `#1B2F36` | window chrome + step rail |
| `panel` | `#223A42` | sidebar / step-panel surface |
| `elevated` | `#2C4750` | inputs, cards, hover surfaces |
| `line` | `#365660` | borders / dividers |
| `muted` | `#9EC1A3` | secondary text, labels, hints |
| `text` | `#E8F1E4` | primary text |
| `accent` | `#70A9A1` | primary interactive / active step |
| `accent_hi` | `#8FC3B9` | hover / pressed |
| `accent2` | `#40798C` | secondary accent |
| `ink` | `#12252B` | dark text on an accent fill |

## Functional / status colours (`theme.STATUS`)

Deliberately *outside* the brand ramp — meaning must not depend on a brand teal, and a
warning must not look like a normal control.

| Role | Hex | Purpose |
|---|---|---|
| `ok` | `#70A9A1` | audit clean (success is on-brand, so it reuses the accent) |
| `warn` | `#E0A64F` | amber — audit warnings, "no effect" notes |
| `danger` | `#E0655A` | red — open / un-welded endpoints |
| `crop` | `#57C8FF` | detection-area rectangle |
| `hilite` | `#8FC3B9` | wash over detected pixels |
| `measure` | `#40798C` | click-to-scale measure lines |
| `guide_box` / `guide_ctr` | `#8FA39C` / `#6FB6AC` | faint export-guide overlays |

## Geometry palette (`theme.GEOMETRY_HEX` / `GEOMETRY_BGR`)

The colours traced entities and the legend use. A **different hue family** from the teal
chrome on purpose, so entities pop and stay mutually distinct. Hex is the source of truth;
BGR is derived for OpenCV.

| Entity | Hex | Hue |
|---|---|---|
| `line` | `#F5B301` | amber |
| `arc` | `#22D3EE` | cyan |
| `circle` | `#4ADE80` | green |
| `spline` | `#E879F9` | magenta |

## Typography (`theme.FONTS` / `theme.SIZES`)

The type treatment is the signature — a measurement instrument, not a form.

- **Display** — `Bahnschrift SemiBold` (a DIN-style technical face shipped with
  Windows 10/11). Used for the brand wordmark, step titles, and let-spaced eyebrows.
  Falls back to `Segoe UI Semibold` if Bahnschrift isn't installed (checked at startup).
- **Body / controls** — `Segoe UI`.
- **Numeric / measurement readouts** — `Consolas` (monospace). Slider values, size
  fields, the audit tally, the histogram — anything that ticks like a readout.

Sizes are named by role in `theme.SIZES` (`title`, `step`, `eyebrow`, `field`, `value`,
`rail_num`, …) so the whole scale can be tuned from one dict.

## How to re-theme

1. Edit hexes in `theme.py` (`T`, `STATUS`, `GEOMETRY_HEX`) and/or families in `FONTS`.
2. BGR forms (`GEOMETRY_BGR`, `CANVAS_BGR`, `GAP_BGR`, …) derive automatically.
3. Restart the GUI. No changes to `img2cad_gui.py` are needed.
