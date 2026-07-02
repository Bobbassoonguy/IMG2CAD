#!/usr/bin/env python3
"""img2cad — single source of truth for colour and type.

Every colour the GUI draws (chrome, geometry, overlays) is defined here so the
whole app can be re-skinned by editing one file. See ``docs/THEME.md`` for the
human-readable rationale.

Design brief: img2cad turns a picture into a machinable DXF, so the palette is a
graded teal system on a jet-black ground (a drafting-table-at-night feel) and the
type pairs a technical DIN-style display face with a monospace numeral face — the
readouts should feel like a caliper, not a web form.

The brand palette (the five swatches the client supplied):

    #1F363D  Jet Black      #40798C  Cerulean       #70A9A1  Tropical Teal
    #9EC1A3  Muted Teal      #CFE0C3  Tea Green

Nothing here imports tkinter or OpenCV — it is pure data + two tiny converters,
so it can be reused or unit-tested anywhere.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Brand palette — the five client colours, verbatim. Everything below derives
# from (or is chosen to sit beside) these.
# --------------------------------------------------------------------------- #
PALETTE = {
    "jet_black":     "#1F363D",
    "cerulean":      "#40798C",
    "tropical_teal": "#70A9A1",
    "muted_teal":    "#9EC1A3",
    "tea_green":     "#CFE0C3",
}


def hex_to_bgr(h: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (B, G, R) for OpenCV."""
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def bgr_to_hex(bgr) -> str:
    """(B, G, R) → '#RRGGBB' for Tk."""
    b, g, r = (int(v) for v in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------- #
# Semantic UI ramp (dark theme). Roles, not colours — widgets reference these
# so a re-theme never has to touch the GUI. Derived from the brand palette:
# jet-black is darkened for surfaces, cerulean tints the mid tones, muted teal
# is the secondary ink, tea-green lightened is the primary ink, tropical teal
# is the one interactive accent.
# --------------------------------------------------------------------------- #
T = {
    "canvas":    "#13232A",   # deepest — the sketch table behind the image
    "bg":        "#1B2F36",   # window chrome + step rail
    "panel":     "#223A42",   # sidebar / step-panel surface
    "elevated":  "#2C4750",   # inputs, cards, hover surfaces
    "line":      "#365660",   # borders / dividers (muted cerulean)
    "muted":     "#9EC1A3",   # secondary text, labels, hints  (Muted Teal)
    "text":      "#E8F1E4",   # primary text  (Tea Green → white)
    "accent":    "#70A9A1",   # primary interactive / active step  (Tropical Teal)
    "accent_hi": "#8FC3B9",   # hover / pressed
    "accent2":   "#40798C",   # secondary accent: measure lines, links  (Cerulean)
    "ink":       "#12252B",   # dark text sitting on an accent fill
}

# --------------------------------------------------------------------------- #
# Functional / status colours. These sit *outside* the brand ramp on purpose:
# no brand teal should be asked to read as "error", and a warning must not look
# like a normal control. Kept deliberately few.
# --------------------------------------------------------------------------- #
STATUS = {
    "ok":      "#70A9A1",   # audit clean (reuses the accent — success is on-brand)
    "warn":    "#E0A64F",   # amber — audit warnings / "no effect" notes
    "danger":  "#E0655A",   # red — open / un-welded endpoints
    "crop":    "#57C8FF",   # detection-area rectangle
    "hilite":  "#8FC3B9",   # wash over detected pixels
    "measure": "#40798C",   # click-to-scale measure lines (Cerulean)
    "guide_box": "#8FA39C",  # faint bounding-box guide
    "guide_ctr": "#6FB6AC",  # faint centreline guide
}

# --------------------------------------------------------------------------- #
# Geometry categorical palette — the colours traced entities are drawn in and
# the legend swatches. Intentionally a *different* hue family from the teal
# chrome so LINE/ARC/CIRCLE/SPLINE pop off the dark canvas and stay mutually
# distinct. Stored as hex (source of truth); BGR derived for OpenCV.
# --------------------------------------------------------------------------- #
GEOMETRY_HEX = {
    "line":   "#F5B301",   # amber
    "arc":    "#22D3EE",   # cyan
    "circle": "#4ADE80",   # green
    "spline": "#E879F9",   # magenta
}
GEOMETRY_BGR = {k: hex_to_bgr(v) for k, v in GEOMETRY_HEX.items()}

# BGR forms of the status/canvas colours OpenCV draws with.
CANVAS_BGR = hex_to_bgr(T["canvas"])
GAP_BGR = hex_to_bgr(STATUS["danger"])
CROP_BGR = hex_to_bgr(STATUS["crop"])
HILITE_BGR = hex_to_bgr(STATUS["hilite"])
MEAS_BGR = hex_to_bgr(STATUS["measure"])
GUIDE_BOX = hex_to_bgr(STATUS["guide_box"])
GUIDE_CTR = hex_to_bgr(STATUS["guide_ctr"])

# --------------------------------------------------------------------------- #
# Typography. The signature: a technical drafting display face for titles and
# a monospace face for every number (a value that changes should tick like an
# instrument readout). Families are names only — the GUI resolves the display
# family at startup and falls back if Bahnschrift isn't installed.
# --------------------------------------------------------------------------- #
FONTS = {
    "display":          "Bahnschrift SemiBold",   # DIN-style; Windows 10/11 stock
    "display_fallback": "Segoe UI Semibold",       # used if Bahnschrift is absent
    "body":             "Segoe UI",
    "body_bold":        "Segoe UI Semibold",
    "mono":             "Consolas",                 # numeric / measurement readouts
}

# Type scale (pt). Named by role, not size, so the whole app rescales here.
SIZES = {
    "title":   16,   # brand wordmark
    "step":    11,   # step-panel title
    "eyebrow":  9,   # let-spaced caps above a panel / section
    "section":  8,   # small accent sub-heading
    "field":    9,   # control labels
    "sub":      9,   # descriptions / hints
    "value":    9,   # numeric readouts (mono)
    "read":     8,   # dense readouts (mono)
    "hint":     9,   # canvas-toolbar hint text
    "status":   9,   # status bar
    "rail_num": 15,  # the ①–⑤ step-rail numerals (display)
    "rail_lbl": 8,   # the step-rail word under the numeral
}
