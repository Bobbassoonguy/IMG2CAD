#!/usr/bin/env python3
"""
img2cad GUI - a small, themed front end for turning an image into an Onshape DXF.

    python img2cad_gui.py [optional image path]

Workflow, top to bottom:
  SOURCE   - open a file or paste from the clipboard (Ctrl+V)
  1 PREPARE - clean up what gets traced: crop, brush away clutter, isolate the
              subject (GrabCut), pick a color to isolate, choose the threshold
  2 TRACE   - pick a preset / mode, then tune the fit (lines + arcs + splines)
  3 SCALE   - set real-world size by measuring the image (click-to-scale) or typing

The interactive tools (Pan / Crop / Brush / Pick color / Measure) live on the
canvas toolbar. All heavy lifting stays in img2cad.py so GUI and CLI never diverge.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
import numpy as np

import img2cad as core

MIN_ZOOM, MAX_ZOOM = 1.0, 12.0
UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "m": 1000.0}   # mm per output unit
# Broader set for the Measure tool's popup (input only) — you can calibrate in any
# common unit regardless of the output unit. Values are millimetres per unit.
MEASURE_UNITS = {
    "mm": 1.0, "cm": 10.0, "m": 1.0e3, "km": 1.0e6, "µm": 1.0e-3,
    "in": 25.4, "ft": 304.8, "yd": 914.4, "mil": 0.0254, "pt": 25.4 / 72.0,
}
PREFS = os.path.join(os.path.expanduser("~"), ".img2cad_gui.json")
IMG_TYPES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")

# Named starting points. Each sets MODE + rough TUNING; Auto-adjust then fine-tunes
# the sliders to the actual image. "Custom" is auto-selected once the user diverges.
PRESETS = {
    "Filled shape":       dict(fit=True, centerline=False, canny=False, invert=False,
                               simplify=2.0, dejag=1.2, weldval=1.5, filletval=0.0, minarea=40),
    "Logo / flat art":    dict(fit=True, centerline=False, canny=False, invert=False,
                               simplify=1.5, dejag=1.5, weldval=2.0, filletval=0.0, minarea=25),
    "Line art (strokes)": dict(fit=True, centerline=True, canny=False, invert=False,
                               simplify=2.0, dejag=1.5, weldval=2.0, filletval=0.0, minarea=20),
    "Outline trace":      dict(fit=True, centerline=False, canny=True, invert=False,
                               simplify=2.0, dejag=1.2, weldval=2.0, filletval=0.0, minarea=15),
}
PRESET_KEYS = ["fit", "centerline", "canny", "invert",
               "simplify", "dejag", "weldval", "filletval", "minarea"]

# Canvas interaction tools (label, mode key, cursor, one-line hint).
TOOLS = [
    ("✥ Pan",                "pan",     "",          "drag to pan · scroll to zoom · double-click to fit"),
    ("⬚ Set detection area", "crop",    "crosshair", "drag a box to limit detection to that region"),
    ("🖌 Brush",              "brush",   "pencil",    "left-drag wipes to background · right-drag adds pixels"),
    ("⦿ Pick",               "pick",    "tcross",    "click a color to trace only that color"),
    ("📏 Measure",            "measure", "crosshair", "click two ends of a known length, then type its real size"),
]
# Friendly names for the status bar (keyed by mode).
MODE_NAMES = {"pan": "pan", "crop": "detection-area", "brush": "brush",
              "pick": "color-pick", "measure": "measure"}

# "Slate + Teal" palette - simple, cool, and it lets the geometry colors pop.
T = {
    "bg": "#0f131a", "panel": "#171d27", "elevated": "#222b38", "line": "#2c3644",
    "text": "#e7ecf4", "muted": "#8593a6", "accent": "#2dd4bf", "accent_hi": "#5eead4",
    "ink": "#04120f", "canvas": "#0b0e13",
}
COLORS = {"line": (1, 179, 245), "arc": (238, 211, 34),
          "circle": (128, 222, 74), "spline": (249, 121, 232)}
GAP_BGR = (60, 60, 255)           # red for open/un-welded endpoints (BGR)
CANVAS_BGR = (19, 14, 11)
GUIDE_BOX = (150, 150, 150)       # faint gray for the bounding box
GUIDE_CTR = (180, 190, 120)       # faint teal for the centerlines
CROP_BGR = (90, 200, 255)         # amber crop rectangle
HILITE_BGR = (150, 235, 90)       # teal-green wash over detected pixels
MEAS_BGR = (80, 180, 255)         # orange for measure lines


def _hex(bgr):
    b, g, r = bgr
    return f"#{r:02x}{g:02x}{b:02x}"


def _paste(dst, src, dx, dy):
    H, W = dst.shape[:2]; sh, sw = src.shape[:2]
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = min(W, dx + sw), min(H, dy + sh)
    if x1 > x0 and y1 > y0:
        dst[y0:y1, x0:x1] = src[y0 - dy:y1 - dy, x0 - dx:x1 - dx]


def _dash(img, p1, p2, color, dash=7, gap=5, th=1):
    p1 = np.asarray(p1, float); p2 = np.asarray(p2, float)
    d = p2 - p1; L = float(np.hypot(*d))
    if L < 1:
        return
    u = d / L
    t = 0.0
    while t < L:
        a = p1 + u * t; b = p1 + u * min(t + dash, L)
        cv2.line(img, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), color, th, cv2.LINE_AA)
        t += dash + gap


class LengthDialog(simpledialog.Dialog):
    """Modal 'how long is this line?' prompt: a number entry + a unit dropdown.

    Returns `(value, unit)` in `self.result` (or None on cancel). Lets you
    calibrate in any common unit — the caller converts to mm via MEASURE_UNITS.
    """
    def __init__(self, parent, default_unit, px_len):
        self._default_unit = default_unit if default_unit in MEASURE_UNITS else "mm"
        self._px_len = px_len
        super().__init__(parent, "Real length")

    def body(self, master):
        self.configure(background=T["bg"])
        master.configure(background=T["panel"])
        ttk.Label(master, text=f"This line is {self._px_len:.0f}px. Its real length is:",
                  style="Field.TLabel").grid(row=0, column=0, columnspan=2,
                                             sticky="w", padx=8, pady=(8, 6))
        self.val = ttk.Entry(master, width=12)
        self.val.grid(row=1, column=0, padx=(8, 4), pady=(0, 8), sticky="ew")
        self.unit = ttk.Combobox(master, values=list(MEASURE_UNITS), state="readonly", width=6)
        self.unit.set(self._default_unit)
        self.unit.grid(row=1, column=1, padx=(0, 8), pady=(0, 8))
        return self.val                 # initial keyboard focus

    def buttonbox(self):
        box = ttk.Frame(self, style="Sidebar.TFrame")
        ttk.Button(box, text="Set scale", style="Accent.TButton",
                   command=self.ok).pack(side="right", padx=(4, 10), pady=8)
        ttk.Button(box, text="Cancel", command=self.cancel).pack(side="right", pady=8)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(side="bottom", fill="x")

    def validate(self):
        try:
            v = float(self.val.get())
        except ValueError:
            messagebox.showwarning("img2cad", "Enter a number for the length.", parent=self)
            return False
        if v <= 0:
            messagebox.showwarning("img2cad", "Length must be greater than zero.", parent=self)
            return False
        self._v = v
        return True

    def apply(self):
        self.result = (self._v, self.unit.get())


class App:
    def __init__(self, root, initial=None):
        self.root = root
        root.title("img2cad — image to Onshape DXF")
        root.configure(background=T["bg"])
        self.path = None
        self.mask = None
        self.color_img = None         # original BGR (for pick / original-view / highlight)
        self._color_dim = None        # dimmed original, un-rotated (background view)
        self._photo = None
        self.zoom, self.ox, self.oy = 1.0, 0.0, 0.0
        self.items = []
        self.draw_img = []            # geometry in image coords
        self.ddraw = []               # geometry rotated into display coords
        self.tally = Counter()
        self.base_img = None          # dimmed mask, un-rotated
        self.dbase = None             # dimmed mask, rotated for display
        self.dcolor = None            # original image, rotated for display
        self.dmask = None             # binary mask, rotated for display (highlight)
        self._base_cache = None       # (key, composited display base) cache for pan/zoom
        self.dw, self.dh = 1, 1       # display canvas size
        self.dbbox = None; self.dimgbox = None
        self.bounds = None            # geometry bounds in image coords
        self.is_prim = True
        self.gap_pts = np.empty((0, 2))   # open endpoints in image coords
        self.dgaps = np.empty((0, 2))     # ... transformed into display coords
        self.audit_txt = ""               # "· 0 audit errors ✓" for the status bar
        self._drag = None
        self._pending = None
        self._rot_pending = None
        self._applying = False            # guard: applying a preset shouldn't flip to Custom
        self._note = ""
        self._sliders = []
        self.tw_mm = None
        self.th_mm = None
        self._editing = False
        self._mask_cache = None       # (key, mask) so slider drags don't re-decode

        # -- image-prep state (crop / brush / GrabCut / color) -------------- #
        self.mode = "pan"
        self.crop = None              # (4,2) image-space quad (detection area), or None
        self.paint_mask = None        # uint8, 255 = brushed-out (forced background)
        self.add_mask = None          # uint8, 255 = brushed-in (forced foreground)
        self.gc_mask = None           # uint8, 255 = GrabCut foreground keep-region
        self.color_active = False
        self.pick_color = (0, 0, 0)   # picked BGR
        self._region_ver = 0          # bumps when brush/GrabCut change (mask-cache key)
        self._crop_start = self._crop_cur = None
        self._brush_stroke = False        # left-drag: erase to background
        self._brush_add = False           # right-drag: add to foreground
        # display transform (image px -> display px), filled by _rebuild_display
        self._disp_R = np.eye(2); self._disp_tv = np.zeros(2); self._disp_Rinv = np.eye(2)
        # -- measure / click-to-scale state --------------------------------- #
        self._meas = []               # in-progress image points (0, 1, or 2)
        self._meas_lines = []         # completed [{p0,p1,dxy,L_mm}], max 2

        self.prefs = self._load_prefs()
        self._apply_theme()
        self._build_widgets()
        if initial and os.path.isfile(initial):
            self.load(initial)

    # -- prefs ------------------------------------------------------------ #
    def _load_prefs(self):
        try:
            with open(PREFS, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_prefs(self):
        data = {"units": self.units.get(), "reference": self.reference.get(),
                "lock": self.lock.get(), "rot": self.rot.get(),
                "show_guides": self.show_guides.get(), "exp_bbox": self.exp_bbox.get(),
                "exp_center": self.exp_center.get()}
        try:
            data.update({
                "preset": self.preset.get(),
                "fit": self.fit.get(), "centerline": self.centerline.get(),
                "canny": self.canny.get(), "invert": self.invert.get(),
                "show_pts": self.show_pts.get(), "show_gaps": self.show_gaps.get(),
                "simplify": self.simplify.get(), "dejag": self.dejag.get(),
                "weldval": self.weldval.get(), "filletval": self.filletval.get(),
                "minarea": self.minarea.get(), "merge": self.merge.get(),
                "thresh_mode": self.thresh_mode.get(), "coltol": self.coltol.get(),
                "brush": self.brush.get(), "bg_view": self.bg_view.get(),
                "highlight": self.highlight.get(),
            })
        except AttributeError:      # called before all widgets exist
            pass
        try:
            with open(PREFS, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _p(self, key, default):
        return self.prefs.get(key, default)

    # -- theme ------------------------------------------------------------ #
    def _apply_theme(self):
        st = ttk.Style(); st.theme_use("clam")
        st.configure(".", background=T["panel"], foreground=T["text"],
                     bordercolor=T["line"], focuscolor=T["panel"])
        for name, bg in [("Sidebar.TFrame", T["panel"]), ("Canvas.TFrame", T["canvas"]),
                         ("Bar.TFrame", T["bg"]), ("Tool.TFrame", T["bg"])]:
            st.configure(name, background=bg)
        st.configure("TLabel", background=T["panel"], foreground=T["text"])
        st.configure("Title.TLabel", foreground=T["text"], font=("Segoe UI Semibold", 15))
        st.configure("Sub.TLabel", foreground=T["muted"], font=("Segoe UI", 9))
        st.configure("Section.TLabel", foreground=T["accent"], font=("Segoe UI", 8, "bold"))
        st.configure("Step.TLabel", foreground=T["text"], font=("Segoe UI Semibold", 10))
        st.configure("Field.TLabel", foreground=T["text"], font=("Segoe UI", 9))
        st.configure("Value.TLabel", foreground=T["accent"], font=("Consolas", 9))
        st.configure("Read.TLabel", foreground=T["muted"], font=("Consolas", 8))
        st.configure("Hint.TLabel", background=T["bg"], foreground=T["muted"], font=("Segoe UI", 9))
        st.configure("Status.TLabel", background=T["bg"], foreground=T["muted"], font=("Segoe UI", 9))
        st.configure("TCheckbutton", background=T["panel"], foreground=T["text"],
                     focuscolor=T["panel"], font=("Segoe UI", 9))
        st.map("TCheckbutton", background=[("active", T["panel"])],
               indicatorcolor=[("selected", T["accent"]), ("!selected", T["elevated"])],
               foreground=[("active", T["text"])])
        st.configure("TButton", background=T["elevated"], foreground=T["text"],
                     bordercolor=T["line"], relief="flat", padding=(10, 7), font=("Segoe UI", 9))
        st.map("TButton", background=[("active", T["line"]), ("pressed", T["line"])])
        st.configure("Accent.TButton", background=T["accent"], foreground=T["ink"],
                     font=("Segoe UI Semibold", 9), padding=(10, 8))
        st.map("Accent.TButton", background=[("active", T["accent_hi"]), ("pressed", T["accent_hi"])])
        # Canvas-toolbar tool buttons: flat on the dark bar, teal when active.
        st.configure("Tool.TButton", background=T["bg"], foreground=T["text"],
                     bordercolor=T["line"], relief="flat", padding=(9, 5), font=("Segoe UI", 9))
        st.map("Tool.TButton", background=[("active", T["elevated"]), ("pressed", T["elevated"])])
        st.configure("ToolOn.TButton", background=T["accent"], foreground=T["ink"],
                     relief="flat", padding=(9, 5), font=("Segoe UI Semibold", 9))
        st.map("ToolOn.TButton", background=[("active", T["accent_hi"]), ("pressed", T["accent_hi"])])
        st.configure("Horizontal.TScale", background=T["panel"], troughcolor=T["elevated"],
                     bordercolor=T["line"], lightcolor=T["accent"], darkcolor=T["accent"])
        st.configure("TSeparator", background=T["line"])
        st.configure("TCombobox", fieldbackground=T["elevated"], background=T["elevated"],
                     foreground=T["text"], arrowcolor=T["text"], bordercolor=T["line"], padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", T["elevated"])],
               foreground=[("readonly", T["text"])])
        st.configure("TEntry", fieldbackground=T["elevated"], foreground=T["text"],
                     insertcolor=T["accent"], bordercolor=T["line"], padding=3)
        st.configure("TSpinbox", fieldbackground=T["elevated"], background=T["elevated"],
                     foreground=T["text"], arrowcolor=T["text"], bordercolor=T["line"], padding=3)
        st.map("TSpinbox", fieldbackground=[("readonly", T["elevated"])])
        st.configure("Vertical.TScrollbar", background=T["elevated"], troughcolor=T["panel"],
                     arrowcolor=T["muted"], bordercolor=T["panel"])
        for opt, val in [("*TCombobox*Listbox.background", T["elevated"]),
                         ("*TCombobox*Listbox.foreground", T["text"]),
                         ("*TCombobox*Listbox.selectBackground", T["accent"]),
                         ("*TCombobox*Listbox.selectForeground", T["ink"])]:
            self.root.option_add(opt, val)

    # -- layout ----------------------------------------------------------- #
    def _build_widgets(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, style="Sidebar.TFrame", width=300)
        outer.grid(row=0, column=0, sticky="ns"); outer.grid_propagate(False)

        # One opaque pinned frame (separator + audit badge + Export button), lifted
        # above the scroll canvas so the taller-than-viewport sidebar can't bleed
        # white through any seam (Windows Tk doesn't clip canvas-embedded windows).
        savewrap = ttk.Frame(outer, style="Sidebar.TFrame")
        savewrap.pack(side="bottom", fill="x")
        ttk.Separator(savewrap).pack(fill="x", padx=16)
        self.audit_lbl = ttk.Label(savewrap, text="", style="Read.TLabel", anchor="center")
        self.audit_lbl.pack(fill="x", padx=16, pady=(8, 5))
        self.save_btn = ttk.Button(savewrap, text="Export…  (DXF · SVG · PDF)",
                                   style="Accent.TButton", command=self.save, state="disabled")
        self.save_btn.pack(fill="x", padx=16, pady=(0, 14))
        self._pinned = (savewrap,)

        sc = tk.Canvas(outer, background=T["panel"], highlightthickness=0, width=274)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=sc.yview, style="Vertical.TScrollbar")
        sc.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); sc.pack(side="left", fill="both", expand=True)
        self._sc, self._vsb, self._vsb_shown = sc, vsb, True
        side = ttk.Frame(sc, style="Sidebar.TFrame", padding=(16, 14))
        sc.create_window((0, 0), window=side, anchor="nw", width=274)
        side.bind("<Configure>", self._update_scroll)
        sc.bind("<Configure>", self._update_scroll)
        self._sidebar = side

        ttk.Label(side, text="img2cad", style="Title.TLabel").pack(anchor="w")
        ttk.Label(side, text="image → Onshape DXF", style="Sub.TLabel").pack(anchor="w")

        # SOURCE
        src = ttk.Frame(side, style="Sidebar.TFrame"); src.pack(fill="x", pady=(12, 0))
        ttk.Button(src, text="Open image…", command=self.pick).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(src, text="⎘ Paste", width=8, command=self.paste_clipboard).pack(side="left")

        self._build_prepare(side)
        self._build_trace(side)
        self._build_scale_section(side)
        self._build_display(side)

        # LEGEND
        self._section(side, "LEGEND")
        leg = ttk.Frame(side, style="Sidebar.TFrame"); leg.pack(fill="x", pady=(0, 4))
        for kind, bgr in list(COLORS.items()) + [("gap", GAP_BGR)]:
            cell = ttk.Frame(leg, style="Sidebar.TFrame"); cell.pack(side="left", padx=(0, 8))
            tk.Label(cell, text="■", fg=_hex(bgr), bg=T["panel"], font=("Segoe UI", 9)).pack(side="left")
            ttk.Label(cell, text=kind, style="Sub.TLabel").pack(side="left", padx=(2, 0))

        # Right: canvas toolbar + studio + status bar
        right = ttk.Frame(self.root, style="Bar.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1); right.columnconfigure(0, weight=1)
        self._build_toolbar(right)
        self.canvas = tk.Canvas(right, background=T["canvas"], highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.canvas.bind("<Configure>", lambda e: self._blit())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._on_press3)      # brush=add · else pan
        self.canvas.bind("<B3-Motion>", self._on_move3)
        self.canvas.bind("<ButtonRelease-3>", self._on_release3)
        self.canvas.bind("<Double-Button-1>", lambda e: self._reset_view())
        self.status = ttk.Label(right, text="Open an image to begin.",
                                style="Status.TLabel", padding=(12, 6))
        self.status.grid(row=2, column=0, sticky="ew")

        self._bind_wheel(self._sidebar)
        self._bind_wheel(self._sc)
        for w in self._pinned:
            w.lift()

        self.root.bind("<Control-v>", lambda e: self.paste_clipboard())
        self.root.bind("<Control-V>", lambda e: self.paste_clipboard())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_mode("pan")

    def _build_toolbar(self, parent):
        bar = ttk.Frame(parent, style="Tool.TFrame", padding=(10, 8))
        bar.grid(row=0, column=0, sticky="ew")
        self._tool_btns = {}
        for label, mode, _cursor, _hint in TOOLS:
            b = ttk.Button(bar, text=label, style="Tool.TButton",
                           command=lambda m=mode: self._set_mode(m))
            b.pack(side="left", padx=(0, 5))
            self._tool_btns[mode] = b
        ttk.Button(bar, text="⤢ Fit", style="Tool.TButton",
                   command=self._reset_view).pack(side="left", padx=(8, 0))
        self.tool_hint = ttk.Label(bar, text="", style="Hint.TLabel")
        self.tool_hint.pack(side="left", padx=(12, 0))

    # -- sidebar sections ------------------------------------------------- #
    def _build_prepare(self, side):
        self._step(side, "1 · PREPARE", "isolate what should be traced")

        # Crop / isolate / reset
        row = ttk.Frame(side, style="Sidebar.TFrame"); row.pack(fill="x", pady=(2, 0))
        ttk.Button(row, text="⛶ Isolate subject", command=self._isolate).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="Reset", width=6, command=self._reset_prep).pack(side="left")
        ttk.Label(side, text="Toolbar: ⬚ Set detection area · 🖌 Brush · ⦿ Pick. "
                  "Brush left-drag erases, right-drag adds pixels.",
                  style="Sub.TLabel", wraplength=250).pack(anchor="w", pady=(4, 0))

        # Brush size + color tolerance + color swatch
        self.brush = self._slider(side, "Brush size (px)", 6.0, 90.0,
                                  self._p("brush", 26.0), fmt="{:.0f}", live=False)
        self.coltol = self._slider(side, "Color range", 4.0, 60.0,
                                   self._p("coltol", 16.0), fmt="{:.0f}", live=False)
        # Only re-traces while a color is actually being isolated (else it's inert).
        self.coltol.trace_add("write", lambda *_: self.color_active and self._schedule())
        crow = ttk.Frame(side, style="Sidebar.TFrame"); crow.pack(fill="x", pady=(6, 0))
        ttk.Label(crow, text="Picked color", style="Field.TLabel").pack(side="left")
        self.swatch = tk.Label(crow, text="  none  ", bg=T["elevated"], fg=T["muted"],
                               font=("Consolas", 8), relief="flat")
        self.swatch.pack(side="left", padx=(6, 6))
        ttk.Button(crow, text="Clear", width=6, command=self._clear_color).pack(side="right")

        # Threshold mode + live histogram
        self._section(side, "THRESHOLD")
        self.thresh_mode = tk.StringVar(value=self._p("thresh_mode", "Auto (Otsu)"))
        self.tcb = ttk.Combobox(side, textvariable=self.thresh_mode, state="readonly",
                                values=["Auto (Otsu)", "Manual", "Adaptive (uneven light)"])
        self.tcb.pack(fill="x")
        self.tcb.bind("<<ComboboxSelected>>", lambda e: self._on_thresh_mode())
        self.threshval = tk.DoubleVar(value=float(self._p("threshval", 128)))
        self.hist = tk.Canvas(side, height=58, background=T["elevated"],
                              highlightthickness=1, highlightbackground=T["line"])
        self.hist.pack(fill="x", pady=(6, 0))
        self.hist.bind("<Configure>", lambda e: self._draw_histogram())
        self.hist.bind("<Button-1>", self._hist_drag)
        self.hist.bind("<B1-Motion>", self._hist_drag)
        self.thresh_hint = ttk.Label(side, text="drag the line to set a manual threshold",
                                     style="Sub.TLabel", wraplength=250)
        self.thresh_hint.pack(anchor="w", pady=(2, 0))

    def _build_trace(self, side):
        self._step(side, "2 · TRACE", "detect & fit clean geometry")

        self.preset = tk.StringVar(value=self._p("preset", "Filled shape"))
        pcb = ttk.Combobox(side, textvariable=self.preset, state="readonly",
                           values=list(PRESETS) + ["Custom"])
        pcb.pack(fill="x", pady=(2, 0))
        pcb.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        self._section(side, "MODE")
        self.fit = tk.BooleanVar(value=self._p("fit", True))
        self.centerline = tk.BooleanVar(value=self._p("centerline", False))
        self.canny = tk.BooleanVar(value=self._p("canny", False))
        self.invert = tk.BooleanVar(value=self._p("invert", False))
        self.show_pts = tk.BooleanVar(value=self._p("show_pts", True))
        self.show_gaps = tk.BooleanVar(value=self._p("show_gaps", True))
        self.merge = tk.BooleanVar(value=self._p("merge", True))
        self._check(side, "Fit lines & arcs", self.fit, mode=True)
        self._check(side, "Centerline (single path)", self.centerline, mode=True)
        self._check(side, "Trace outlines (Canny)", self.canny, mode=True)
        self._check(side, "Invert (light shape)", self.invert, mode=True)

        self._section(side, "TUNING")
        self.auto_btn = ttk.Button(side, text="✦  Auto-adjust", style="Accent.TButton",
                                   command=self.auto, state="disabled")
        self.auto_btn.pack(fill="x", pady=(0, 2))
        self.simplify = self._slider(side, "Simplify", 0.5, 8.0, self._p("simplify", 2.0))
        self.dejag = self._slider(side, "De-jag", 0.0, 4.0, self._p("dejag", 1.2))
        self.weldval = self._slider(side, "Weld gaps", 0.0, 6.0, self._p("weldval", 1.5))
        self.filletval = self._slider(side, "Fillet corners", 0.0, 25.0, self._p("filletval", 0.0))
        self.minarea = self._slider(side, "Ignore specks", 0.0, 1000.0,
                                    self._p("minarea", 40.0), fmt="{:.0f}")
        self._check(side, "Merge similar entities", self.merge, mode=True)

    def _build_scale_section(self, side):
        self._step(side, "3 · SCALE / OUTPUT", "set true real-world size")
        self._build_scale(side)

    def _build_display(self, side):
        self._section(side, "DISPLAY")
        self.bg_view = tk.StringVar(value=self._p("bg_view", "Dimmed mask"))
        self.highlight = tk.BooleanVar(value=self._p("highlight", False))
        r = ttk.Frame(side, style="Sidebar.TFrame"); r.pack(fill="x", pady=(2, 2))
        ttk.Label(r, text="Background", style="Field.TLabel").pack(side="left")
        bcb = ttk.Combobox(r, textvariable=self.bg_view, state="readonly", width=13,
                           values=["Dimmed mask", "Original image"])
        bcb.pack(side="right")
        bcb.bind("<<ComboboxSelected>>", lambda e: self._on_view_change())
        self._check(side, "Highlight detected pixels", self.highlight, view=True)
        self._check(side, "Show points", self.show_pts, redraw_only=True)
        self._check(side, "Flag open gaps (red)", self.show_gaps, redraw_only=True)
        ttk.Checkbutton(side, text="Show guides in viewer", variable=self.show_guides,
                        command=lambda: (self._blit(), self._save_prefs())).pack(anchor="w", pady=1)

    def _on_close(self):
        self._save_prefs()
        self.root.destroy()

    def _bind_wheel(self, widget):
        # The histogram uses left-drag for the threshold; wheel still scrolls sidebar.
        widget.bind("<MouseWheel>", self._sidebar_scroll)
        widget.bind("<Button-4>", self._sidebar_scroll)
        widget.bind("<Button-5>", self._sidebar_scroll)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _sidebar_scroll(self, event):
        if not self._vsb_shown:
            return "break"
        step = -1 if (getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4) else 1
        self._sc.yview_scroll(step, "units")
        return "break"

    def _update_scroll(self, *_):
        need = self._sidebar.winfo_reqheight()
        have = self._sc.winfo_height()
        self._sc.configure(scrollregion=(0, 0, self._sidebar.winfo_reqwidth(), need))
        overflow = need > have + 1
        if overflow and not self._vsb_shown:
            self._vsb.pack(side="right", fill="y", before=self._sc); self._vsb_shown = True
        elif not overflow and self._vsb_shown:
            self._vsb.pack_forget(); self._vsb_shown = False; self._sc.yview_moveto(0)

    def _section(self, parent, text):
        ttk.Separator(parent).pack(fill="x", pady=(11, 0))
        ttk.Label(parent, text=text, style="Section.TLabel").pack(anchor="w", pady=(6, 1))

    def _step(self, parent, text, sub):
        ttk.Separator(parent).pack(fill="x", pady=(14, 0))
        ttk.Label(parent, text=text, style="Step.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(parent, text=sub, style="Sub.TLabel").pack(anchor="w", pady=(0, 2))

    def _check(self, parent, text, var, redraw_only=False, mode=False, view=False):
        if redraw_only:
            cmd = lambda: (self._blit(), self._save_prefs())
        elif view:
            cmd = self._on_view_change
        elif mode:
            cmd = self._on_mode_change
        else:
            cmd = self._schedule
        ttk.Checkbutton(parent, text=text, variable=var, command=cmd).pack(anchor="w", pady=1)

    def _on_mode_change(self):
        """A MODE/TUNING toggle diverges from the preset -> mark Custom, then recompute."""
        if not self._applying:
            self.preset.set("Custom")
        self._sync_threshold_ui()       # Canny toggle changes whether threshold applies
        self._schedule()

    def _on_view_change(self):
        # Toggling Background/Highlight may now need dcolor/dmask that _rebuild_display
        # skips when the feature is off — rebuild so the warp happens on the toggle
        # (infrequent) instead of on every slider drag.
        self._rebuild_display()
        self._blit(); self._save_prefs()

    def _slider(self, parent, text, lo, hi, init, fmt="{:.1f}", live=True):
        row = ttk.Frame(parent, style="Sidebar.TFrame"); row.pack(fill="x", pady=(7, 0))
        ttk.Label(row, text=text, style="Field.TLabel").pack(side="left")
        val = ttk.Label(row, text=fmt.format(init), style="Value.TLabel"); val.pack(side="right")
        var = tk.DoubleVar(value=init)

        def on(*_):
            val.config(text=fmt.format(var.get()))
            if live:
                if not self._applying:
                    self.preset.set("Custom")
                self._schedule()

        ttk.Scale(parent, from_=lo, to=hi, variable=var, command=on,
                  style="Horizontal.TScale").pack(fill="x", pady=(2, 0))
        self._sliders.append((var, val, fmt))
        return var

    def _sync_labels(self):
        for var, lbl, fmt in self._sliders:
            lbl.config(text=fmt.format(var.get()))

    def _build_scale(self, parent):
        self.units = tk.StringVar(value=self._p("units", "mm"))
        self.reference = tk.StringVar(value=self._p("reference", "Geometry"))
        self.lock = tk.BooleanVar(value=self._p("lock", True))
        self.rot = tk.StringVar(value=self._p("rot", "0"))
        self.wvar = tk.StringVar(value="—")
        self.hvar = tk.StringVar(value="—")
        self.show_guides = tk.BooleanVar(value=self._p("show_guides", True))
        self.exp_bbox = tk.BooleanVar(value=self._p("exp_bbox", False))
        self.exp_center = tk.BooleanVar(value=self._p("exp_center", False))

        r1 = ttk.Frame(parent, style="Sidebar.TFrame"); r1.pack(fill="x", pady=(2, 0))
        ttk.Label(r1, text="Units", style="Field.TLabel").pack(side="left")
        ttk.Combobox(r1, textvariable=self.units, values=list(UNIT_MM), width=5,
                     state="readonly").pack(side="left", padx=(4, 10))
        ttk.Label(r1, text="Measure", style="Field.TLabel").pack(side="left")
        ttk.Combobox(r1, textvariable=self.reference, values=["Geometry", "Image"], width=9,
                     state="readonly").pack(side="left", padx=(4, 0))

        r2 = ttk.Frame(parent, style="Sidebar.TFrame"); r2.pack(fill="x", pady=(7, 0))
        ttk.Label(r2, text="Rotate°", style="Field.TLabel").pack(side="left")
        sp = ttk.Spinbox(r2, from_=0, to=359, increment=1, textvariable=self.rot, width=4,
                         wrap=True, command=self._on_rotate)
        sp.pack(side="left", padx=(4, 4))
        sp.bind("<Return>", lambda e: self._on_rotate())
        sp.bind("<KeyRelease>", lambda e: self._schedule_rotate())
        ttk.Button(r2, text="⟳ 90°", width=6, command=self._rotate_cw).pack(side="left")
        ttk.Checkbutton(r2, text="Lock aspect", variable=self.lock,
                        command=self._on_lock).pack(side="right")

        wr = ttk.Frame(parent, style="Sidebar.TFrame"); wr.pack(fill="x", pady=(8, 0))
        ttk.Label(wr, text="Width", style="Field.TLabel", width=6).pack(side="left")
        self.wentry = ttk.Entry(wr, textvariable=self.wvar, width=8); self.wentry.pack(side="left")
        self.wunit = ttk.Label(wr, text="mm", style="Sub.TLabel"); self.wunit.pack(side="left", padx=(4, 0))
        hr = ttk.Frame(parent, style="Sidebar.TFrame"); hr.pack(fill="x", pady=(4, 0))
        ttk.Label(hr, text="Height", style="Field.TLabel", width=6).pack(side="left")
        self.hentry = ttk.Entry(hr, textvariable=self.hvar, width=8); self.hentry.pack(side="left")
        self.hunit = ttk.Label(hr, text="mm", style="Sub.TLabel"); self.hunit.pack(side="left", padx=(4, 0))

        # Click-to-scale: measure a known length on the image to set true size.
        self.meas_lbl = ttk.Label(parent, text="📏 Measure tool: click two ends of a "
                                  "known length.", style="Sub.TLabel", wraplength=250)
        self.meas_lbl.pack(anchor="w", pady=(7, 0))
        mrow = ttk.Frame(parent, style="Sidebar.TFrame"); mrow.pack(fill="x", pady=(2, 0))
        ttk.Button(mrow, text="Clear scale lines", command=self._clear_measure).pack(
            side="left", fill="x", expand=True)

        self.readout = ttk.Label(parent, text="", style="Read.TLabel"); self.readout.pack(anchor="w", pady=(6, 2))

        ttk.Checkbutton(parent, text="Export bounding box", variable=self.exp_bbox,
                        command=self._save_prefs).pack(anchor="w", pady=1)
        ttk.Checkbutton(parent, text="Export centerlines", variable=self.exp_center,
                        command=self._save_prefs).pack(anchor="w", pady=1)

        for w in (self.wentry, self.hentry):
            w.bind("<Return>", self._on_size_edit)
            w.bind("<FocusOut>", self._on_size_edit)
        self.units.trace_add("write", lambda *_: self._on_units())
        self.reference.trace_add("write", lambda *_: self._on_reference())

    # -- scale logic ------------------------------------------------------ #
    def _ref_px(self):
        if self.reference.get() == "Image" or self.bounds is None:
            h, w = (self.mask.shape[:2] if self.mask is not None else (1, 1))
            return float(w), float(h)
        x0, y0, x1, y1 = self.bounds
        return max(x1 - x0, 1.0), max(y1 - y0, 1.0)

    def _refresh_scale_fields(self):
        rw, rh = self._ref_px()
        if self.tw_mm is None:
            self.tw_mm, self.th_mm = rw, rh
        u = UNIT_MM[self.units.get()]
        self._editing = True
        self.wvar.set(f"{self.tw_mm / u:.2f}")
        self.hvar.set(f"{self.th_mm / u:.2f}")
        self._editing = False
        self.wunit.config(text=self.units.get()); self.hunit.config(text=self.units.get())
        self._update_readout()

    def _on_size_edit(self, *_):
        if self._editing:
            return
        u = UNIT_MM[self.units.get()]; rw, rh = self._ref_px()
        try:
            w_val = float(self.wvar.get()); h_val = float(self.hvar.get())
        except ValueError:
            return
        cur_w = (self.tw_mm or 0) / u
        if self.lock.get():
            if abs(h_val - (self.th_mm or 0) / u) > 1e-6 and abs(w_val - cur_w) <= 1e-6:
                self.th_mm = max(h_val, 1e-6) * u
                self.tw_mm = self.th_mm * rw / rh
            else:
                self.tw_mm = max(w_val, 1e-6) * u
                self.th_mm = self.tw_mm * rh / rw
        else:
            self.tw_mm = max(w_val, 1e-6) * u
            self.th_mm = max(h_val, 1e-6) * u
        self._refresh_scale_fields()
        self._rebuild_display(); self._blit()

    def _on_units(self):
        self.wunit.config(text=self.units.get()); self.hunit.config(text=self.units.get())
        self._refresh_scale_fields(); self._save_prefs()

    def _on_reference(self):
        self._refresh_scale_fields(); self._rebuild_display(); self._blit(); self._save_prefs()

    def _rot_deg(self):
        try:
            return float(self.rot.get() or 0) % 360.0
        except ValueError:
            return 0.0

    def _rotate_cw(self):
        self.rot.set(str(int((self._rot_deg() + 90) % 360)))
        self._on_rotate()

    def _schedule_rotate(self):
        if self._rot_pending is not None:
            self.root.after_cancel(self._rot_pending)
        self._rot_pending = self.root.after(150, self._do_rotate)

    def _do_rotate(self):
        self._rot_pending = None; self._on_rotate()

    def _on_rotate(self):
        self._rebuild_display(); self._update_readout(); self._blit(); self._save_prefs()

    def _on_lock(self):
        if self.lock.get() and self.tw_mm is not None:
            rw, rh = self._ref_px()
            self.th_mm = self.tw_mm * rh / rw
            self._refresh_scale_fields(); self._rebuild_display(); self._blit()
        self._save_prefs()

    def _scale_ratio(self):
        # Locked aspect never stretches the preview — so changing the detection
        # area (which shifts the geometry bounds) can't distort the display. A
        # non-uniform stretch only appears when the user explicitly unlocks aspect
        # (e.g. a 2-line measure), which sets lock=False.
        if self.tw_mm is None or self.lock.get():
            return 1.0
        rw, rh = self._ref_px()
        sx = self.tw_mm / rw; sy = self.th_mm / rh
        return sy / sx if sx > 1e-9 else 1.0

    def _update_readout(self):
        if self.mask is None:
            return
        opt = self._opts(); h, w = self.mask.shape[:2]
        tf, _, _ = core.make_transform(opt, h, w)
        ob = core.output_bounds(self.items, self.is_prim, tf, opt, w, h)
        if ob is None:
            return
        x0, y0, x1, y1 = ob
        ow, oh = x1 - x0, y1 - y0
        g = np.gcd(int(round(ow)), int(round(oh))) or 1
        u = self.units.get()
        self.readout.config(text=f"output ≈ {ow:.1f} × {oh:.1f} {u}   ·   "
                                 f"aspect {int(round(ow))//g}:{int(round(oh))//g}")

    # -- source ----------------------------------------------------------- #
    def pick(self):
        p = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"),
                       ("All", "*.*")])
        if p:
            self.load(p)

    def paste_clipboard(self):
        try:
            from PIL import ImageGrab
        except ImportError:
            messagebox.showinfo("Paste image",
                                "Pasting from the clipboard needs Pillow.\n\n"
                                "Install it with:\n    pip install pillow")
            return
        try:
            grab = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not read the clipboard:\n{e}")
            return
        if isinstance(grab, list):
            files = [p for p in grab if p.lower().endswith(IMG_TYPES)]
            if files:
                self.load(files[0]); return
            grab = None
        if grab is None:
            messagebox.showinfo("Paste image",
                                "No image on the clipboard.\n\n"
                                "Copy an image (or use the Windows snipping tool) and try again.")
            return
        tmp = os.path.join(tempfile.gettempdir(), "img2cad_clipboard.png")
        try:
            grab.convert("RGBA").save(tmp)
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not save the pasted image:\n{e}")
            return
        self._mask_cache = None
        self.load(tmp)
        self._flash("Pasted image from clipboard")

    # -- presets / auto --------------------------------------------------- #
    def apply_preset(self, *_):
        name = self.preset.get()
        rec = PRESETS.get(name)
        if not rec:
            return
        self._applying = True
        self.fit.set(rec["fit"]); self.centerline.set(rec["centerline"])
        self.canny.set(rec["canny"]); self.invert.set(rec["invert"])
        self.simplify.set(rec["simplify"]); self.dejag.set(rec["dejag"])
        self.weldval.set(rec["weldval"]); self.filletval.set(rec["filletval"])
        self.minarea.set(rec["minarea"])
        self._sync_labels()
        self._applying = False
        self._sync_threshold_ui()       # a preset may flip Canny on/off
        self._flash(f"Preset: {name}")
        if self.path:
            self.recompute()
        self._save_prefs()

    def auto(self):
        if not self.path:
            return
        try:
            s = core.auto_adjust(self.path)
        except SystemExit as e:
            messagebox.showerror("img2cad", str(e)); return
        self.simplify.set(s["tol"]); self.dejag.set(s["depixel"]); self.weldval.set(s["weld"])
        self.minarea.set(min(float(s["min_area"]), 1000.0))
        self._sync_labels()
        if self._pending is not None:
            self.root.after_cancel(self._pending); self._pending = None
        self._flash(f"Auto-tuned → simplify {s['tol']}, de-jag {s['depixel']}, weld {s['weld']}")
        self.recompute()

    # -- options ---------------------------------------------------------- #
    def _color_tol(self):
        v = float(self.coltol.get())
        return (int(v), int(min(120, v * 4 + 20)), int(min(120, v * 4 + 20)))

    def _opts(self):
        centerline = self.centerline.get()
        u = UNIT_MM[self.units.get()]
        rw, rh = self._ref_px()
        sx = (self.tw_mm / u) / rw if self.tw_mm else 1.0
        sy = (self.th_mm / u) / rh if self.th_mm else 1.0
        if self.lock.get():
            sy = sx        # locked aspect = uniform scale everywhere, so the exported
                           # DXF matches the un-stretched preview (_scale_ratio == 1.0)
                           # even after the geometry bounds shift (e.g. a crop).
        tm = self.thresh_mode.get()
        return core.Options(
            invert=self.invert.get(),
            canny=self.canny.get() and not centerline,
            adaptive=tm.startswith("Adaptive"),
            threshold=int(self.threshval.get()) if tm == "Manual" else -1,
            color=tuple(self.pick_color) if self.color_active else None,
            color_tol=self._color_tol(),
            crop=None,        # the GUI applies the detection area as a region polygon
                              # (see _region) so it stays exact under rotation; opt.crop
                              # is the CLI-only axis-aligned form.
            fit=self.fit.get() or centerline,
            centerline=centerline,
            tol=float(self.simplify.get()),
            depixel=float(self.dejag.get()),
            weld=float(self.weldval.get()),
            fillet=float(self.filletval.get()),
            merge=self.merge.get(),
            min_area=float(self.minarea.get()),
            epsilon=0.0002 + (self.simplify.get() / 8.0) * 0.008,
            smooth=float(self.dejag.get()),
            units=self.units.get(),
            scale_x=sx,
            scale_y=sy,
            rotate=-self._rot_deg(),
            export_bbox=self.exp_bbox.get(),
            export_centerlines=self.exp_center.get(),
            guide_ref="image" if self.reference.get() == "Image" else "geometry",
        )

    def load(self, path):
        self.path = path
        self.root.title(f"img2cad — {os.path.basename(path)}")
        self.auto_btn.config(state="normal"); self.save_btn.config(state="normal")
        # Fresh image: drop any prep state from the previous one.
        self.crop = None; self.paint_mask = None; self.add_mask = None; self.gc_mask = None
        self.color_active = False; self._region_ver += 1
        self._clear_measure(recompute=False)
        try:
            self.color_img = core.load_bgr(path)
            self._color_dim = (self.color_img.astype(np.float32) * 0.6).astype(np.uint8)
        except Exception:
            self.color_img = self._color_dim = None
        self._update_color_swatch()
        self._reset_view(redraw=False)
        self.recompute()
        self._sync_threshold_ui()

    def _schedule(self):
        if self._pending is not None:
            self.root.after_cancel(self._pending)
        self._pending = self.root.after(70, self._do_recompute)

    def _do_recompute(self):
        self._pending = None; self.recompute()

    def recompute(self):
        if not self.path:
            return
        opt = self._opts()
        try:
            self.mask = self._get_mask(opt)
            self._build_draw(opt)
        except ModuleNotFoundError as e:
            pkg = "scikit-image" if (e.name or "").startswith("skimage") else e.name
            messagebox.showerror("img2cad", f"Missing dependency '{e.name}'.\n\n"
                                            f"Install it with:\n    pip install {pkg}")
            return
        except SystemExit as e:
            messagebox.showerror("img2cad", f"Could not open image:\n{e}")
            return
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not process image:\n{e}")
            return
        self.base_img = (cv2.cvtColor(self.mask, cv2.COLOR_GRAY2BGR) * 0.22).astype(np.uint8)
        self._update_audit(opt)
        self._rebuild_display()
        self._refresh_scale_fields()
        self._blit()

    def _update_audit(self, opt):
        try:
            h, w = self.mask.shape[:2]
            _, errs = core.audit_items(self.items, opt, h, w)
        except Exception:
            self.audit_txt = ""
            self.audit_lbl.config(text="", foreground=T["muted"])
            return
        n = sum(self.tally.values())
        ng = len(self.gap_pts)
        gap_is_fault = ng > 0 and not opt.centerline
        if errs == 0 and not gap_is_fault:
            self.audit_txt = "0 audit errors ✓"
            col = T["accent"]
            badge = f"{n} entities · 0 audit errors ✓"
            if opt.centerline and ng:
                badge += f"  ({ng} open end" + ("s" if ng != 1 else "") + ")"
        else:
            bits = []
            if errs:
                bits.append(f"{errs} audit error" + ("s" if errs != 1 else ""))
            if gap_is_fault:
                bits.append(f"{ng} open gap" + ("s" if ng != 1 else ""))
            self.audit_txt = " · ".join(bits) + " ⚠"
            col = "#f0a850"
            badge = f"{n} entities · " + self.audit_txt
        self.audit_lbl.config(text=badge, foreground=col)

    def _crop_rect(self):
        """Axis-aligned image-space bounds of the detection-area quad (for GrabCut)."""
        if self.crop is None:
            return None
        q = np.asarray(self.crop, float)
        return (float(q[:, 0].min()), float(q[:, 1].min()),
                float(q[:, 0].max()), float(q[:, 1].max()))

    def _region(self):
        """Combined keep-region from the detection area + GrabCut + masking brush.

        The detection area is applied as the *exact quad the user drew* (a polygon),
        not an axis-aligned bounding box — so it stays correct when the image is
        rotated (an axis-aligned screen box maps to a tilted quad in image space).
        """
        reg = self.gc_mask
        if self.crop is not None and self.color_img is not None:
            cm = np.zeros(self.color_img.shape[:2], np.uint8)
            cv2.fillConvexPoly(cm, np.round(np.asarray(self.crop)).astype(np.int32), 255)
            reg = cm if reg is None else cv2.bitwise_and(reg, cm)
        if self.paint_mask is not None:
            keep = cv2.bitwise_not(self.paint_mask)     # painted pixels -> excluded
            reg = keep if reg is None else cv2.bitwise_and(reg, keep)
        return reg

    def _get_mask(self, opt):
        key = (self.path, opt.invert, opt.canny, opt.blur, opt.threshold, opt.adaptive,
               opt.adaptive_block, opt.adaptive_c, opt.color, opt.color_tol, opt.crop,
               self._region_ver)
        if self._mask_cache is not None and self._mask_cache[0] == key:
            return self._mask_cache[1]
        mask = core.load_binary(self.path, opt, self._region(), self.add_mask)
        self._mask_cache = (key, mask)
        return mask

    def _build_draw(self, opt):
        items = core.build_items(self.mask, opt)
        self.items = items
        self.is_prim = opt.fit or opt.centerline
        if self.is_prim:
            prims = [p for lst in items for p in lst]
            self.draw_img = [{"kind": p["kind"], "pts": core.primitive_points(p),
                              "ends": core.primitive_endpoints(p), "closed": p["kind"] == "circle"}
                             for p in prims]
            self.tally = Counter(p["kind"] for p in prims)
        else:
            self.draw_img = [{"kind": "spline", "pts": pts, "closed": not opt.canny,
                              "ends": pts[[0, -1]] if len(pts) >= 2 else pts}
                             for pts in items if len(pts) >= 2]
            self.tally = Counter({"spline": len(self.draw_img)})
        self.bounds = core.geometry_bounds(items, self.is_prim)
        if self.is_prim:
            gap_tol = max(float(self.weldval.get()), 1.0) * 1.5
            self.gap_pts = core.open_endpoints(items, gap_tol)
        else:
            self.gap_pts = np.empty((0, 2))

    def _rebuild_display(self):
        """Rotate the base image + geometry into display space (WYSIWYG preview)."""
        if self.base_img is None:
            return
        h, w = self.base_img.shape[:2]
        ang = -self._rot_deg()
        Mr = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
        ratio = self._scale_ratio()
        srx, sry = (1.0, ratio) if ratio <= 1 else (1.0 / ratio, 1.0)
        c = np.array([w / 2.0, h / 2.0])
        S, ts = np.diag([srx, sry]), c - np.diag([srx, sry]) @ np.array([w / 2.0, h / 2.0])
        Rr, tr_ = Mr[:, :2], Mr[:, 2]
        L = Rr @ S
        Tt = Rr @ ts + tr_
        M = np.hstack([L, Tt.reshape(2, 1)])
        corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], float)
        tc = corners @ M[:, :2].T + M[:, 2]
        mn, mx = tc.min(0), tc.max(0)
        M[:, 2] -= mn
        nw, nh = max(int(np.ceil(mx[0] - mn[0])), 1), max(int(np.ceil(mx[1] - mn[1])), 1)
        self.dbase = cv2.warpAffine(self.base_img, M, (nw, nh),
                                    flags=cv2.INTER_NEAREST, borderValue=CANVAS_BGR)
        # dcolor / dmask feed only the "Original image" background and the
        # "Highlight detected pixels" overlay — warp them lazily, so a plain slider
        # drag (both features off, the default) doesn't pay two extra full-image
        # warpAffine passes. _on_view_change re-runs this when a toggle turns on.
        want_color = self._color_dim is not None and self.bg_view.get() == "Original image"
        self.dcolor = (cv2.warpAffine(self._color_dim, M, (nw, nh), flags=cv2.INTER_NEAREST,
                                      borderValue=CANVAS_BGR) if want_color else self.dbase)
        want_mask = self.mask is not None and self.highlight.get()
        self.dmask = (cv2.warpAffine(self.mask, M, (nw, nh), flags=cv2.INTER_NEAREST,
                                     borderValue=0) if want_mask else None)
        self._base_cache = None
        R, tv = M[:, :2], M[:, 2]
        self._disp_R, self._disp_tv, self._disp_Rinv = R, tv, np.linalg.inv(R)

        def tr(p):
            return np.asarray(p, float) @ R.T + tv

        self.ddraw = [{"kind": d["kind"], "closed": d["closed"], "pts": tr(d["pts"]),
                       "ends": tr(d["ends"]) if len(d["ends"]) else d["ends"]}
                      for d in self.draw_img]
        self.dgaps = tr(self.gap_pts) if len(self.gap_pts) else np.empty((0, 2))
        self.dw, self.dh = nw, nh
        self.dimgbox = (0.0, 0.0, float(nw), float(nh))
        if self.ddraw:
            allp = np.vstack([d["pts"] for d in self.ddraw])
            self.dbbox = (float(allp[:, 0].min()), float(allp[:, 1].min()),
                          float(allp[:, 0].max()), float(allp[:, 1].max()))
        else:
            self.dbbox = None

    def _composed_base(self):
        """Display background per the current view + highlight toggles (cached)."""
        key = (self.bg_view.get(), self.highlight.get())
        if self._base_cache is not None and self._base_cache[0] == key:
            return self._base_cache[1]
        base = self.dcolor if self.bg_view.get() == "Original image" else self.dbase
        img = base.copy()
        if self.highlight.get() and self.dmask is not None:
            m = self.dmask > 0
            if m.any():
                tint = np.array(HILITE_BGR, np.float32)
                img[m] = (img[m].astype(np.float32) * 0.35 + tint * 0.65).astype(np.uint8)
        self._base_cache = (key, img)
        return img

    # -- view / render ---------------------------------------------------- #
    def _fit_scale(self, cw, ch):
        return min(cw / self.dw, ch / self.dh)

    def _eff_off(self):
        """Current display->canvas scale and offset (for coord mapping / overlays)."""
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        eff = self._fit_scale(cw, ch) * self.zoom
        return eff, np.array([self.ox, self.oy])

    def _blit(self):
        if getattr(self, "dbase", None) is None:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            self.canvas.after(40, self._blit); return
        frame = self._render_frame(cw, ch)
        ok, buf = cv2.imencode(".png", frame)
        if ok:
            self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        t = self.tally
        parts = "  ".join(f"{v} {k}" for k, v in t.items()) or "nothing detected"
        head = f"{self._note}   ·   " if self._note else ""
        rot = f"  rot {int(self._rot_deg())}°" if self._rot_deg() else ""
        audit = f"   ·   {self.audit_txt}" if self.audit_txt else ""
        self.status.config(text=f"{head}{sum(t.values())} entities: {parts}{rot}{audit}"
                                f"   ·   zoom {self.zoom:.1f}×  ({MODE_NAMES.get(self.mode, self.mode)} tool)")

    def _render_frame(self, cw, ch):
        """Compose the full canvas image (background + geometry + overlays) offscreen."""
        base = self._composed_base()
        w, h = self.dw, self.dh
        eff = self._fit_scale(cw, ch) * self.zoom
        dw, dh = w * eff, h * eff
        self.ox = (dw - cw) / 2 if dw <= cw else min(max(self.ox, 0.0), dw - cw)
        self.oy = (dh - ch) / 2 if dh <= ch else min(max(self.oy, 0.0), dh - ch)

        frame = np.empty((ch, cw, 3), np.uint8); frame[:] = CANVAS_BGR
        sx0 = max(0.0, self.ox / eff); sy0 = max(0.0, self.oy / eff)
        sx1 = min(float(w), (self.ox + cw) / eff); sy1 = min(float(h), (self.oy + ch) / eff)
        if sx1 > sx0 and sy1 > sy0:
            crop = base[int(sy0):int(np.ceil(sy1)), int(sx0):int(np.ceil(sx1))]
            dx0 = int(round(int(sx0) * eff - self.ox)); dy0 = int(round(int(sy0) * eff - self.oy))
            cwi = int(round(crop.shape[1] * eff)); chi = int(round(crop.shape[0] * eff))
            if cwi > 0 and chi > 0:
                _paste(frame, cv2.resize(crop, (cwi, chi), interpolation=cv2.INTER_NEAREST), dx0, dy0)

        off = np.array([self.ox, self.oy])
        if self.show_guides.get():
            self._draw_guides(frame, eff, off)
        for d in self.ddraw:
            ip = np.round(d["pts"] * eff - off).astype(np.int32)
            cv2.polylines(frame, [ip], bool(d["closed"]), COLORS[d["kind"]], 2, cv2.LINE_AA)
        if self.show_pts.get():
            for d in self.ddraw:
                for e in d["ends"]:
                    px, py = np.round(np.asarray(e) * eff - off).astype(int)
                    cv2.circle(frame, (int(px), int(py)), 3, (255, 255, 255), -1, cv2.LINE_AA)
                    cv2.circle(frame, (int(px), int(py)), 3, (30, 30, 30), 1, cv2.LINE_AA)
        if self.show_gaps.get() and len(self.dgaps):
            for g in self.dgaps:
                px, py = np.round(np.asarray(g) * eff - off).astype(int)
                cv2.circle(frame, (int(px), int(py)), 5, GAP_BGR, -1, cv2.LINE_AA)
                cv2.circle(frame, (int(px), int(py)), 5, (255, 255, 255), 1, cv2.LINE_AA)
        self._draw_crop(frame, eff, off)
        self._draw_measure(frame, eff, off)
        self._frame = frame
        return frame

    def _img_to_canvas(self, pts, eff, off):
        """Map image-space points -> canvas pixels via the display transform."""
        pts = np.atleast_2d(np.asarray(pts, float))
        disp = pts @ self._disp_R.T + self._disp_tv
        return disp * eff - off

    def _draw_guides(self, frame, eff, off):
        box = self.dbbox
        if self.reference.get() == "Image" or box is None:
            box = getattr(self, "dimgbox", None)
        if box is None:
            return
        x0, y0, x1, y1 = box

        def sp(x, y):
            return (x * eff - off[0], y * eff - off[1])
        c = [sp(x0, y0), sp(x1, y0), sp(x1, y1), sp(x0, y1)]
        for a, b in zip(c, c[1:] + c[:1]):
            _dash(frame, a, b, GUIDE_BOX)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        _dash(frame, sp(cx, y0), sp(cx, y1), GUIDE_CTR)
        _dash(frame, sp(x0, cy), sp(x1, cy), GUIDE_CTR)

    def _draw_crop(self, frame, eff, off):
        if self.crop is None:
            return
        c = self._img_to_canvas(self.crop, eff, off)   # the 4 quad corners -> canvas
        for a, b in zip(c, np.roll(c, -1, axis=0)):
            _dash(frame, a, b, CROP_BGR, dash=10, gap=6, th=2)

    def _draw_measure(self, frame, eff, off):
        pts = []
        for ln in self._meas_lines:
            pts.append((ln["p0"], ln["p1"], ln.get("L_mm")))
        if len(self._meas) == 2:
            pts.append((self._meas[0], self._meas[1], None))
        for p0, p1, L in pts:
            a, b = self._img_to_canvas([p0, p1], eff, off)
            cv2.line(frame, tuple(a.astype(int)), tuple(b.astype(int)), MEAS_BGR, 2, cv2.LINE_AA)
            for q in (a, b):
                cv2.circle(frame, tuple(q.astype(int)), 4, MEAS_BGR, -1, cv2.LINE_AA)
                cv2.circle(frame, tuple(q.astype(int)), 4, (255, 255, 255), 1, cv2.LINE_AA)
        for q in self._meas:
            a = self._img_to_canvas([q], eff, off)[0]
            cv2.circle(frame, tuple(a.astype(int)), 4, MEAS_BGR, -1, cv2.LINE_AA)

    def _flash(self, text, ms=3500):
        self._note = text; self.root.after(ms, self._clear_note)

    def _clear_note(self):
        self._note = ""
        if getattr(self, "dbase", None) is not None:
            self._blit()

    def _reset_view(self, redraw=True):
        self.zoom, self.ox, self.oy = MIN_ZOOM, 0.0, 0.0
        if redraw:
            self._blit()

    # -- canvas tool modes ------------------------------------------------ #
    def _set_mode(self, mode):
        self.mode = mode
        for m, b in self._tool_btns.items():
            b.configure(style="ToolOn.TButton" if m == mode else "Tool.TButton")
        cursor = next((c for _l, m, c, _h in TOOLS if m == mode), "")
        hint = next((h for _l, m, _c, h in TOOLS if m == mode), "")
        self.canvas.configure(cursor=cursor)
        self.tool_hint.config(text=hint)
        if mode != "measure":
            self._meas = []
        if getattr(self, "dbase", None) is not None:
            self._blit()

    def _canvas_to_img(self, cx, cy):
        """Map a canvas pixel -> image-space point (inverse display transform)."""
        if self.dbase is None:
            return None
        eff, off = self._eff_off()
        if eff <= 0:
            return None
        disp = (np.array([cx, cy], float) + off) / eff
        return self._disp_Rinv @ (disp - self._disp_tv)

    def _on_press(self, event):
        if self.mode == "pan":
            self._on_pan_start(event)
        elif self.mode == "crop":
            self._crop_start = self._crop_cur = (event.x, event.y)
        elif self.mode == "brush":
            self._brush_stroke = True
            self._paint_at(event.x, event.y)
        elif self.mode == "pick":
            self._pick_at(event.x, event.y)
        elif self.mode == "measure":
            self._measure_click(event.x, event.y)

    def _on_move(self, event):
        if self.mode == "pan":
            self._on_pan_move(event)
        elif self.mode == "crop" and self._crop_start:
            self._crop_cur = (event.x, event.y)
            self._draw_crop_preview()
        elif self.mode == "brush" and self._brush_stroke:
            self._paint_at(event.x, event.y)

    def _on_release(self, event):
        if self.mode == "pan":
            self._drag = None
        elif self.mode == "crop" and self._crop_start:
            self._finish_crop()
        elif self.mode == "brush":
            self._brush_stroke = False
            self._region_ver += 1
            self.canvas.delete("brushfx")
            self._flash("Brushed area wiped to background")
            self.recompute()

    # Right button: an add-brush while the Brush tool is active, else pan-anywhere.
    def _on_press3(self, event):
        if self.mode == "brush":
            self._brush_add = True
            self._paint_add(event.x, event.y)
        else:
            self._on_pan_start(event)

    def _on_move3(self, event):
        if self.mode == "brush" and self._brush_add:
            self._paint_add(event.x, event.y)
        else:
            self._on_pan_move(event)

    def _on_release3(self, event):
        if self.mode == "brush" and self._brush_add:
            self._brush_add = False
            self._region_ver += 1
            self.canvas.delete("brushfx")
            self._flash("Brushed pixels added to foreground")
            self.recompute()
        else:
            self._drag = None

    # -- crop ------------------------------------------------------------- #
    def _draw_crop_preview(self):
        self.canvas.delete("croppreview")
        if not self._crop_start:
            return
        x0, y0 = self._crop_start; x1, y1 = self._crop_cur
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=_hex(CROP_BGR),
                                     width=2, dash=(6, 4), tags="croppreview")

    def _finish_crop(self):
        self.canvas.delete("croppreview")
        (x0, y0), (x1, y1) = self._crop_start, self._crop_cur
        self._crop_start = self._crop_cur = None
        if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:      # a click, not a drag -> clear it
            if self.crop is not None:
                self.crop = None
                self._region_ver += 1
                self._flash("Detection area cleared")
                self.recompute()
            return
        # Map the four screen-box corners (in order) to image space and keep them as
        # a quad — under rotation this is a tilted rectangle, so a polygon fill traces
        # exactly what was drawn instead of an enlarged axis-aligned bounding box.
        corners = [self._canvas_to_img(*p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        if any(c is None for c in corners):
            return
        self.crop = np.array(corners, float)
        self._region_ver += 1
        self._flash("Detection area set")
        self.recompute()

    # -- brush ------------------------------------------------------------ #
    def _paint_at(self, cx, cy):
        self._paint(cx, cy, add=False)

    def _paint_add(self, cx, cy):
        self._paint(cx, cy, add=True)

    def _paint(self, cx, cy, add):
        """Stamp the brush into the erase (add=False) or add (add=True) mask."""
        p = self._canvas_to_img(cx, cy)
        if p is None or self.mask is None:
            return
        attr = "add_mask" if add else "paint_mask"
        if getattr(self, attr) is None:
            setattr(self, attr, np.zeros(self.mask.shape[:2], np.uint8))
        eff, _ = self._eff_off()
        r_img = max(1, int(round(float(self.brush.get()) / max(eff, 1e-6))))
        cv2.circle(getattr(self, attr), (int(round(p[0])), int(round(p[1]))), r_img, 255, -1)
        r_scr = max(2, int(round(float(self.brush.get()))))
        fx = _hex(HILITE_BGR) if add else _hex(GAP_BGR)      # green=add, red=erase
        self.canvas.create_oval(cx - r_scr, cy - r_scr, cx + r_scr, cy + r_scr,
                                fill=fx, outline="", tags="brushfx")

    # -- pick color ------------------------------------------------------- #
    def _pick_at(self, cx, cy):
        p = self._canvas_to_img(cx, cy)
        if p is None or self.color_img is None:
            return
        h, w = self.color_img.shape[:2]
        x, y = int(round(p[0])), int(round(p[1]))
        if not (0 <= x < w and 0 <= y < h):
            return
        self.pick_color = tuple(int(v) for v in self.color_img[y, x])
        self.color_active = True
        self._update_color_swatch()
        self._sync_threshold_ui()
        self._flash(f"Isolating color {_hex(self.pick_color)}")
        self.recompute()

    def _update_color_swatch(self):
        if self.color_active:
            hx = _hex(self.pick_color)
            self.swatch.config(text=f" {hx} ", bg=hx,
                               fg="#000" if sum(self.pick_color) > 360 else "#fff")
        else:
            self.swatch.config(text="  none  ", bg=T["elevated"], fg=T["muted"])

    def _clear_color(self):
        if not self.color_active:
            return
        self.color_active = False
        self._update_color_swatch()
        self._sync_threshold_ui()
        self._flash("Color isolation off")
        self.recompute()

    # -- isolate / reset prep --------------------------------------------- #
    def _isolate(self):
        if not self.path:
            return
        self._flash("Isolating subject… (GrabCut, a moment)")
        self.status.config(text="Isolating subject with GrabCut…")
        self.root.update_idletasks()
        try:
            self.gc_mask = core.grabcut_foreground(self.path, self._crop_rect())
        except Exception as e:
            messagebox.showerror("img2cad", f"GrabCut failed:\n{e}")
            self.status.config(text="GrabCut failed — try a smaller region.")
            return
        self._region_ver += 1
        self._flash("Isolated subject (GrabCut)")
        self.recompute()

    def _reset_prep(self):
        self.crop = None
        self.paint_mask = None
        self.add_mask = None
        self.gc_mask = None
        self.color_active = False
        self._update_color_swatch()
        self._sync_threshold_ui()
        self._region_ver += 1
        self._flash("Prep reset")
        if self.path:
            self.recompute()

    # -- threshold + histogram ------------------------------------------- #
    def _threshold_active(self):
        """The threshold panel only affects the trace in the plain binarize path —
        Canny outline-tracing and color isolation bypass it entirely."""
        return not (self.canny.get() or self.color_active)

    def _sync_threshold_ui(self):
        """Enable/disable the threshold panel and explain why when it's inactive."""
        active = self._threshold_active()
        self.tcb.configure(state="readonly" if active else "disabled")
        if active:
            self.thresh_hint.config(text="drag the line to set a manual threshold",
                                    foreground=T["muted"])
        else:
            why = "Canny outline mode" if self.canny.get() else "color isolation"
            self.thresh_hint.config(text=f"⚠ threshold has no effect in {why} — "
                                    "turn it off to use the threshold.",
                                    foreground="#f0a850")
        self._draw_histogram()

    def _on_thresh_mode(self):
        self._draw_histogram()
        self._save_prefs()
        self.recompute()

    def _hist_drag(self, event):
        if not self._threshold_active():
            return
        w = max(self.hist.winfo_width(), 1)
        val = float(np.clip(event.x / w * 255.0, 0, 255))
        self.threshval.set(val)
        if not self.thresh_mode.get().startswith("Manual"):
            self.thresh_mode.set("Manual")
        self._draw_histogram()
        self._schedule()

    def _draw_histogram(self):
        c = self.hist
        c.delete("all")
        w = max(c.winfo_width(), 1); h = max(c.winfo_height(), 1)
        if self.color_img is None or w < 4:
            return
        if not self._threshold_active():                # greyed out + reason
            c.create_text(w / 2, h / 2, text="threshold not used in this mode",
                          fill=T["muted"], font=("Segoe UI", 8))
            return
        gray = cv2.cvtColor(self.color_img, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [128], [0, 256]).flatten()
        hist = np.log1p(hist)
        mx = hist.max() or 1.0
        bw = w / 128.0
        for i, v in enumerate(hist):
            bh = (v / mx) * (h - 4)
            c.create_rectangle(i * bw, h - bh, (i + 1) * bw, h,
                               fill=T["muted"], outline="")
        # Otsu marker (faint) when auto; manual threshold line (bright) otherwise.
        mode = self.thresh_mode.get()
        if mode.startswith("Auto"):
            otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            x = otsu / 255.0 * w
            c.create_line(x, 0, x, h, fill=T["line"], width=1, dash=(3, 2))
        elif mode.startswith("Manual"):
            x = float(self.threshval.get()) / 255.0 * w
            c.create_line(x, 0, x, h, fill=T["accent"], width=2)
        else:
            c.create_text(w / 2, h / 2, text="adaptive · local threshold",
                          fill=T["muted"], font=("Segoe UI", 8))

    # -- measure / click-to-scale ---------------------------------------- #
    def _clear_measure(self, recompute=True):
        self._meas = []
        self._meas_lines = []
        if hasattr(self, "meas_lbl"):
            self.meas_lbl.config(text="📏 Measure tool: click two ends of a known length.")
        if recompute and getattr(self, "dbase", None) is not None:
            self._blit()

    def _measure_click(self, cx, cy):
        p = self._canvas_to_img(cx, cy)
        if p is None:
            return
        self._meas.append(p)
        if len(self._meas) < 2:
            self._blit()
            return
        p0, p1 = self._meas[0], self._meas[1]
        self._meas = []
        dist = float(np.hypot(*(p1 - p0)))
        if dist < 2:
            self._blit(); return
        dlg = LengthDialog(self.root, self.units.get(), dist)
        if not dlg.result:
            self._blit(); return
        val, unit = dlg.result
        line = {"p0": p0, "p1": p1, "dxy": (p1 - p0), "L_mm": val * MEASURE_UNITS[unit]}
        # A 3rd line starts a fresh calibration.
        if len(self._meas_lines) >= 2:
            self._meas_lines = []
        self._meas_lines.append(line)
        self._apply_measure()

    def _apply_measure(self):
        rw, rh = self._ref_px()
        lines = self._meas_lines
        u = self.units.get()
        if len(lines) >= 2:
            res = core.solve_scale_2line(lines[0]["dxy"], lines[0]["L_mm"],
                                         lines[1]["dxy"], lines[1]["L_mm"])
            if res is not None:
                sx, sy = res
                self.tw_mm, self.th_mm = rw * sx, rh * sy
                self.lock.set(False)
                self.meas_lbl.config(text=f"📏 2 lines → aspect unlocked "
                                     f"({self.tw_mm/UNIT_MM[u]:.1f}×{self.th_mm/UNIT_MM[u]:.1f} {u}).")
                self._finish_measure()
                return
            self._meas_lines = lines[:1]        # degenerate 2nd line: fall back to 1-line
        s = core.solve_scale_1line(self._meas_lines[0]["dxy"], self._meas_lines[0]["L_mm"])
        if s is None:
            return
        self.tw_mm, self.th_mm = rw * s, rh * s
        self.lock.set(True)
        self.meas_lbl.config(text=f"📏 Scaled: {self._meas_lines[0]['L_mm']/UNIT_MM[u]:.1f} {u} "
                             "over the line. Add a ⟂ line to unlock aspect.")
        self._finish_measure()

    def _finish_measure(self):
        self._refresh_scale_fields()
        self._rebuild_display()
        self._blit()
        self._save_prefs()

    # -- interaction (pan / zoom) ---------------------------------------- #
    def _on_wheel(self, event):
        if self.dbase is None:
            return
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        nz = min(max(self.zoom * (1.25 if up else 1 / 1.25), MIN_ZOOM), MAX_ZOOM)
        if nz == self.zoom:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        base = self._fit_scale(cw, ch)
        ix = (self.ox + event.x) / (base * self.zoom); iy = (self.oy + event.y) / (base * self.zoom)
        self.zoom = nz
        self.ox = ix * base * self.zoom - event.x; self.oy = iy * base * self.zoom - event.y
        self._blit()

    def _on_pan_start(self, event):
        self._drag = (event.x, event.y)

    def _on_pan_move(self, event):
        if self._drag is None:
            return
        self.ox -= event.x - self._drag[0]; self.oy -= event.y - self._drag[1]
        self._drag = (event.x, event.y); self._blit()

    # -- export ----------------------------------------------------------- #
    def save(self):
        if not self.path or not self.items:
            return
        default = os.path.splitext(self.path)[0] + ".dxf"
        out = filedialog.asksaveasfilename(
            defaultextension=".dxf",
            initialfile=os.path.basename(default),
            filetypes=[("DXF — Onshape sketch", "*.dxf"),
                       ("SVG — laser / vector", "*.svg"),
                       ("PDF — print / share", "*.pdf")])
        if not out:
            return
        opt = self._opts()
        fmt = out.rsplit(".", 1)[-1].lower() if "." in os.path.basename(out) else "dxf"
        try:
            mask = self._get_mask(opt)
            items = core.build_items(mask, opt)
            if not any(items):
                messagebox.showwarning("img2cad", "No geometry detected — nothing to export.\n"
                                                  "Try Invert, Canny, or a lower 'Ignore specks'.")
                return
            h, w = mask.shape[:2]
            tally = core.export_file(items, out, opt, h, w)
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not write {fmt.upper()}:\n{e}")
            return
        breakdown = ", ".join(f"{v} {k}" for k, v in tally.items() if k != "total")
        hint = ("\n\nIn Onshape: right-click a sketch plane → Import DXF/DWG."
                if fmt == "dxf" else "")
        messagebox.showinfo("Saved",
                            f"Wrote {tally.get('total', 0)} entities ({breakdown}) to:\n{out}{hint}")


def _icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(base, "img2cad.ico"),
              os.path.join(base, "packaging", "img2cad.ico")):
        if os.path.exists(p):
            return p
    return None


def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    icon = _icon_path()
    if icon:
        try:
            root.iconbitmap(default=icon)
        except Exception:
            pass
    App(root, initial)
    root.minsize(1040, 660)
    root.update_idletasks()
    sh = root.winfo_screenheight()
    h = max(660, min(940, sh - 80))
    root.geometry(f"1300x{h}")
    root.mainloop()


if __name__ == "__main__":
    main()
