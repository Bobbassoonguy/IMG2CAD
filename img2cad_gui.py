#!/usr/bin/env python3
"""
img2cad GUI - a small, themed front end for turning an image into an Onshape DXF.

    python img2cad_gui.py [optional image path]

Open an image, hit "Auto-adjust" (or tweak the sidebar), set the output size in the
Scale panel, then "Save DXF". All the heavy lifting lives in img2cad.py.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

import img2cad as core

MIN_ZOOM, MAX_ZOOM = 1.0, 12.0
UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "m": 1000.0}   # mm per output unit
PREFS = os.path.join(os.path.expanduser("~"), ".img2cad_gui.json")

# "Slate + Teal" palette - simple, cool, and it lets the geometry colors pop.
T = {
    "bg": "#0f131a", "panel": "#171d27", "elevated": "#222b38", "line": "#2c3644",
    "text": "#e7ecf4", "muted": "#8593a6", "accent": "#2dd4bf", "accent_hi": "#5eead4",
    "ink": "#04120f", "canvas": "#0b0e13",
}
COLORS = {"line": (1, 179, 245), "arc": (238, 211, 34),
          "circle": (128, 222, 74), "spline": (249, 121, 232)}
CANVAS_BGR = (19, 14, 11)
GUIDE_BOX = (150, 150, 150)       # faint gray for the bounding box
GUIDE_CTR = (180, 190, 120)       # faint teal for the centerlines


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


class App:
    def __init__(self, root, initial=None):
        self.root = root
        root.title("img2cad — image to Onshape DXF")
        root.configure(background=T["bg"])
        self.path = None
        self.mask = None
        self._photo = None
        self.zoom, self.ox, self.oy = 1.0, 0.0, 0.0
        self.items = []
        self.draw_img = []            # geometry in image coords
        self.ddraw = []               # geometry rotated into display coords
        self.tally = Counter()
        self.base_img = None          # dimmed background, un-rotated
        self.dbase = None             # dimmed background, rotated for display
        self.dw, self.dh = 1, 1       # display canvas size
        self.dbbox = None; self.dimgbox = None
        self.bounds = None            # geometry bounds in image coords
        self.is_prim = True
        self._drag = None
        self._pending = None
        self._rot_pending = None
        self._note = ""
        self._sliders = []
        # Canonical scale = target OUTPUT size (object-axis) in mm for the current
        # reference. Derived on demand into units/px; None until an image loads.
        self.tw_mm = None
        self.th_mm = None
        self._editing = False
        self._mask_cache = None       # (key, mask) so slider drags don't re-decode

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
                         ("Bar.TFrame", T["bg"])]:
            st.configure(name, background=bg)
        st.configure("TLabel", background=T["panel"], foreground=T["text"])
        st.configure("Title.TLabel", foreground=T["text"], font=("Segoe UI Semibold", 15))
        st.configure("Sub.TLabel", foreground=T["muted"], font=("Segoe UI", 9))
        st.configure("Section.TLabel", foreground=T["muted"], font=("Segoe UI", 8, "bold"))
        st.configure("Field.TLabel", foreground=T["text"], font=("Segoe UI", 9))
        st.configure("Value.TLabel", foreground=T["accent"], font=("Consolas", 9))
        st.configure("Read.TLabel", foreground=T["muted"], font=("Consolas", 8))
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

        # Scrollable sidebar: outer holds a pinned Save button + a scrolling canvas.
        outer = ttk.Frame(self.root, style="Sidebar.TFrame", width=298)
        outer.grid(row=0, column=0, sticky="ns"); outer.grid_propagate(False)

        savewrap = ttk.Frame(outer, style="Sidebar.TFrame")
        savewrap.pack(side="bottom", fill="x", padx=16, pady=(6, 14))
        self.save_btn = ttk.Button(savewrap, text="Save DXF…", command=self.save, state="disabled")
        self.save_btn.pack(fill="x")
        ttk.Separator(outer).pack(side="bottom", fill="x", padx=16)

        sc = tk.Canvas(outer, background=T["panel"], highlightthickness=0, width=272)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=sc.yview, style="Vertical.TScrollbar")
        sc.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); sc.pack(side="left", fill="both", expand=True)
        self._sc, self._vsb, self._vsb_shown = sc, vsb, True
        side = ttk.Frame(sc, style="Sidebar.TFrame", padding=(16, 14))
        sc.create_window((0, 0), window=side, anchor="nw", width=272)
        side.bind("<Configure>", self._update_scroll)
        sc.bind("<Configure>", self._update_scroll)
        self._sidebar = side

        ttk.Label(side, text="img2cad", style="Title.TLabel").pack(anchor="w")
        ttk.Label(side, text="image → Onshape DXF", style="Sub.TLabel").pack(anchor="w")
        ttk.Button(side, text="Open image…", command=self.pick).pack(fill="x", pady=(12, 0))

        # MODE (Auto-adjust never touches these)
        self._section(side, "MODE")
        self.fit = tk.BooleanVar(value=True)
        self.centerline = tk.BooleanVar(value=False)
        self.canny = tk.BooleanVar(value=False)
        self.invert = tk.BooleanVar(value=False)
        self.show_pts = tk.BooleanVar(value=True)
        self._check(side, "Fit lines & arcs", self.fit)
        self._check(side, "Centerline (single path)", self.centerline)
        self._check(side, "Trace outlines (Canny)", self.canny)
        self._check(side, "Invert (light shape)", self.invert)
        self._check(side, "Show points", self.show_pts, redraw_only=True)

        # TUNING (Auto-adjust sets the sliders here, except Fillet)
        self._section(side, "TUNING")
        self.auto_btn = ttk.Button(side, text="✦  Auto-adjust", style="Accent.TButton",
                                   command=self.auto, state="disabled")
        self.auto_btn.pack(fill="x", pady=(0, 2))
        self.simplify = self._slider(side, "Simplify", 0.5, 8.0, 2.0)
        self.dejag = self._slider(side, "De-jag", 0.0, 4.0, 1.2)
        self.weldval = self._slider(side, "Weld gaps", 0.0, 6.0, 1.5)
        self.filletval = self._slider(side, "Fillet corners", 0.0, 25.0, 0.0)
        self.minarea = self._slider(side, "Ignore specks", 0.0, 1000.0, 40.0, fmt="{:.0f}")

        self._section(side, "SCALE / OUTPUT")
        self._build_scale(side)

        self._section(side, "LEGEND")
        leg = ttk.Frame(side, style="Sidebar.TFrame"); leg.pack(fill="x", pady=(0, 4))
        for kind, bgr in COLORS.items():
            cell = ttk.Frame(leg, style="Sidebar.TFrame"); cell.pack(side="left", padx=(0, 9))
            tk.Label(cell, text="■", fg=_hex(bgr), bg=T["panel"], font=("Segoe UI", 9)).pack(side="left")
            ttk.Label(cell, text=kind, style="Sub.TLabel").pack(side="left", padx=(3, 0))

        # Right: canvas studio + status bar
        right = ttk.Frame(self.root, style="Bar.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1); right.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(right, background=T["canvas"], highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas.bind("<Configure>", lambda e: self._blit())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<Double-Button-1>", lambda e: self._reset_view())
        self.status = ttk.Label(right, text="Open an image to begin.",
                                style="Status.TLabel", padding=(12, 6))
        self.status.grid(row=1, column=0, sticky="ew")

        # Mouse-wheel scrolls the sidebar from anywhere over it (each widget
        # eats its own wheel events, so bind them all rather than bind_all,
        # which would also fire while zooming over the image canvas).
        self._bind_wheel(self._sidebar)
        self._bind_wheel(self._sc)

    def _bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._sidebar_scroll)
        widget.bind("<Button-4>", self._sidebar_scroll)
        widget.bind("<Button-5>", self._sidebar_scroll)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _sidebar_scroll(self, event):
        if not self._vsb_shown:                     # nothing to scroll
            return "break"
        step = -1 if (getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4) else 1
        self._sc.yview_scroll(step, "units")
        return "break"

    def _update_scroll(self, *_):
        """Show the scrollbar (and enable wheel) only when the sidebar overflows."""
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

    def _check(self, parent, text, var, redraw_only=False):
        cmd = self._blit if redraw_only else self._schedule
        ttk.Checkbutton(parent, text=text, variable=var, command=cmd).pack(anchor="w", pady=1)

    def _slider(self, parent, text, lo, hi, init, fmt="{:.1f}"):
        row = ttk.Frame(parent, style="Sidebar.TFrame"); row.pack(fill="x", pady=(7, 0))
        ttk.Label(row, text=text, style="Field.TLabel").pack(side="left")
        val = ttk.Label(row, text=fmt.format(init), style="Value.TLabel"); val.pack(side="right")
        var = tk.DoubleVar(value=init)

        def on(*_):
            val.config(text=fmt.format(var.get())); self._schedule()

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
        sp.bind("<KeyRelease>", lambda e: self._schedule_rotate())   # debounce typing
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

        self.readout = ttk.Label(parent, text="", style="Read.TLabel"); self.readout.pack(anchor="w", pady=(6, 2))

        ttk.Checkbutton(parent, text="Show guides in viewer", variable=self.show_guides,
                        command=lambda: (self._blit(), self._save_prefs())).pack(anchor="w", pady=1)
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
        if self.tw_mm is None:                          # default: 1 px -> 1 mm
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
        # Which field the user actually changed decides the driver (so a locked
        # Height edit is honored, not silently overwritten by Width).
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
        self._rebuild_display(); self._blit()           # aspect ratio may have changed

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
        if self.lock.get() and self.tw_mm is not None:  # snap height to keep aspect
            rw, rh = self._ref_px()
            self.th_mm = self.tw_mm * rh / rw
            self._refresh_scale_fields(); self._rebuild_display(); self._blit()
        self._save_prefs()

    def _scale_ratio(self):
        """sy/sx for the current settings (1.0 unless aspect is unlocked & unequal)."""
        if self.tw_mm is None:
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

    # -- actions ---------------------------------------------------------- #
    def pick(self):
        p = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"),
                       ("All", "*.*")])
        if p:
            self.load(p)

    def auto(self):
        """Auto-adjust ONLY the tuning sliders (not MODE, not Fillet)."""
        if not self.path:
            return
        try:
            s = core.auto_adjust(self.path)
        except SystemExit as e:
            messagebox.showerror("img2cad", str(e)); return
        self.simplify.set(s["tol"]); self.dejag.set(s["depixel"]); self.weldval.set(s["weld"])
        self.minarea.set(min(float(s["min_area"]), 1000.0))
        self._sync_labels()
        if self._pending is not None:                   # cancel the setters' scheduled pass
            self.root.after_cancel(self._pending); self._pending = None
        self._flash(f"Auto-tuned → simplify {s['tol']}, de-jag {s['depixel']}, weld {s['weld']}")
        self.recompute()

    def _opts(self):
        centerline = self.centerline.get()
        u = UNIT_MM[self.units.get()]
        rw, rh = self._ref_px()
        # units/px so the reference's object-axis output size equals the target.
        sx = (self.tw_mm / u) / rw if self.tw_mm else 1.0
        sy = (self.th_mm / u) / rh if self.th_mm else 1.0
        return core.Options(
            invert=self.invert.get(),
            canny=self.canny.get() and not centerline,
            fit=self.fit.get() or centerline,
            centerline=centerline,
            tol=float(self.simplify.get()),
            depixel=float(self.dejag.get()),
            weld=float(self.weldval.get()),
            fillet=float(self.filletval.get()),
            min_area=float(self.minarea.get()),
            epsilon=0.0002 + (self.simplify.get() / 8.0) * 0.008,
            smooth=float(self.dejag.get()),
            units=self.units.get(),
            scale_x=sx,
            scale_y=sy,
            rotate=-self._rot_deg(),   # spinbox = clockwise; export transform is CCW

            export_bbox=self.exp_bbox.get(),
            export_centerlines=self.exp_center.get(),
            guide_ref="image" if self.reference.get() == "Image" else "geometry",
        )

    def load(self, path):
        self.path = path
        self.root.title(f"img2cad — {os.path.basename(path)}")
        self.auto_btn.config(state="normal"); self.save_btn.config(state="normal")
        self._reset_view(redraw=False)
        self.recompute()

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
            self._build_draw(opt)                       # may import skimage / fit splines
        except ModuleNotFoundError as e:
            pkg = "scikit-image" if (e.name or "").startswith("skimage") else e.name
            messagebox.showerror("img2cad", f"Missing dependency '{e.name}'.\n\n"
                                            f"Install it with:\n    pip install {pkg}")
            return
        except SystemExit as e:            # load_binary raises SystemExit on unreadable images
            messagebox.showerror("img2cad", f"Could not open image:\n{e}")
            return
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not process image:\n{e}")
            return
        self.base_img = (cv2.cvtColor(self.mask, cv2.COLOR_GRAY2BGR) * 0.22).astype(np.uint8)
        self._rebuild_display()
        self._refresh_scale_fields()
        self._blit()

    def _get_mask(self, opt):
        """Binarize, caching on just the params that affect the mask (sliders don't)."""
        key = (self.path, opt.invert, opt.canny, opt.blur, opt.threshold)
        if self._mask_cache is not None and self._mask_cache[0] == key:
            return self._mask_cache[1]
        mask = core.load_binary(self.path, opt)
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

    def _rebuild_display(self):
        """Rotate the base image + geometry into display space so the preview is WYSIWYG."""
        if self.base_img is None:
            return
        h, w = self.base_img.shape[:2]
        ang = -self._rot_deg()          # cv2 +angle is CCW; negative => clockwise content
        Mr = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
        # Show a non-uniform stretch (aspect unlocked) too: scale about center, then
        # rotate. Normalize so the image only ever shrinks along one axis.
        ratio = self._scale_ratio()
        srx, sry = (1.0, ratio) if ratio <= 1 else (1.0 / ratio, 1.0)
        c = np.array([w / 2.0, h / 2.0])
        S, ts = np.diag([srx, sry]), c - np.diag([srx, sry]) @ np.array([w / 2.0, h / 2.0])
        Rr, tr_ = Mr[:, :2], Mr[:, 2]
        L = Rr @ S
        T = Rr @ ts + tr_
        M = np.hstack([L, T.reshape(2, 1)])
        corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], float)
        tc = corners @ M[:, :2].T + M[:, 2]
        mn, mx = tc.min(0), tc.max(0)
        M[:, 2] -= mn
        nw, nh = max(int(np.ceil(mx[0] - mn[0])), 1), max(int(np.ceil(mx[1] - mn[1])), 1)
        self.dbase = cv2.warpAffine(self.base_img, M, (nw, nh),
                                    flags=cv2.INTER_NEAREST, borderValue=CANVAS_BGR)
        R, tv = M[:, :2], M[:, 2]

        def tr(p):
            return np.asarray(p, float) @ R.T + tv

        self.ddraw = [{"kind": d["kind"], "closed": d["closed"], "pts": tr(d["pts"]),
                       "ends": tr(d["ends"]) if len(d["ends"]) else d["ends"]}
                      for d in self.draw_img]
        self.dw, self.dh = nw, nh
        self.dimgbox = (0.0, 0.0, float(nw), float(nh))
        # Recalculate the box as the AABB of the *rotated* geometry (don't tilt it).
        if self.ddraw:
            allp = np.vstack([d["pts"] for d in self.ddraw])
            self.dbbox = (float(allp[:, 0].min()), float(allp[:, 1].min()),
                          float(allp[:, 0].max()), float(allp[:, 1].max()))
        else:
            self.dbbox = None

    # -- view / render ---------------------------------------------------- #
    def _fit_scale(self, cw, ch):
        return min(cw / self.dw, ch / self.dh)

    def _blit(self):
        if getattr(self, "dbase", None) is None:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            self.canvas.after(40, self._blit); return
        w, h = self.dw, self.dh
        eff = self._fit_scale(cw, ch) * self.zoom
        dw, dh = w * eff, h * eff
        self.ox = (dw - cw) / 2 if dw <= cw else min(max(self.ox, 0.0), dw - cw)
        self.oy = (dh - ch) / 2 if dh <= ch else min(max(self.oy, 0.0), dh - ch)

        frame = np.empty((ch, cw, 3), np.uint8); frame[:] = CANVAS_BGR
        sx0 = max(0.0, self.ox / eff); sy0 = max(0.0, self.oy / eff)
        sx1 = min(float(w), (self.ox + cw) / eff); sy1 = min(float(h), (self.oy + ch) / eff)
        if sx1 > sx0 and sy1 > sy0:
            crop = self.dbase[int(sy0):int(np.ceil(sy1)), int(sx0):int(np.ceil(sx1))]
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

        ok, buf = cv2.imencode(".png", frame)
        if ok:
            self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        t = self.tally
        parts = "  ".join(f"{v} {k}" for k, v in t.items()) or "nothing detected"
        head = f"{self._note}   ·   " if self._note else ""
        rot = f"  rot {int(self._rot_deg())}°" if self._rot_deg() else ""
        self.status.config(text=f"{head}{sum(t.values())} entities: {parts}{rot}"
                                f"   ·   zoom {self.zoom:.1f}×  (scroll=zoom · drag=pan · dbl-click=fit)")

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

    # -- interaction ------------------------------------------------------ #
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

    def save(self):
        if not self.path or not self.items:
            return
        default = os.path.splitext(self.path)[0] + ".dxf"
        out = filedialog.asksaveasfilename(defaultextension=".dxf",
                                           initialfile=os.path.basename(default),
                                           filetypes=[("DXF", "*.dxf")])
        if not out:
            return
        # Rebuild geometry from the exact opt we're exporting with, so a pending
        # debounced recompute can't leave self.items in a shape that mismatches opt.
        opt = self._opts()
        try:
            mask = self._get_mask(opt)
            items = core.build_items(mask, opt)
            if not any(items):
                messagebox.showwarning("img2cad", "No geometry detected — nothing to export.\n"
                                                  "Try Invert, Canny, or a lower 'Ignore specks'.")
                return
            h, w = mask.shape[:2]
            tally = core.write_dxf(items, out, opt, h, w)
        except Exception as e:
            messagebox.showerror("img2cad", f"Could not write DXF:\n{e}")
            return
        breakdown = ", ".join(f"{v} {k}" for k, v in tally.items() if k != "total")
        messagebox.showinfo("Saved",
                            f"Wrote {tally.get('total', 0)} entities ({breakdown}) to:\n{out}\n\n"
                            "In Onshape: right-click a sketch plane → Import DXF/DWG.")


def _icon_path():
    """Locate img2cad.ico whether running from source or a PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(base, "img2cad.ico"),
              os.path.join(base, "packaging", "img2cad.ico")):
        if os.path.exists(p):
            return p
    return None


def main():
    # Note: a standalone .exe already gets a stable taskbar identity from its own
    # path, which matches the installed shortcut — so launching (from the pin or the
    # file association) groups under one button. We deliberately do NOT set an
    # explicit AppUserModelID here: the shortcut can't carry the same id, so setting
    # one would split the pin from the running window into two taskbar buttons.
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    icon = _icon_path()
    if icon:
        try:
            root.iconbitmap(default=icon)
        except Exception:
            pass
    App(root, initial)
    root.minsize(960, 640)
    root.geometry("1240x940")   # tall enough that the sidebar fits without scrolling
    root.mainloop()


if __name__ == "__main__":
    main()
