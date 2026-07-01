#!/usr/bin/env python3
"""
img2cad - Turn a PNG/graphic into a clean DXF you can import into Onshape.

Pipeline:  image -> binarize -> find contours -> simplify -> (optional) smooth
           -> write DXF (splines or polylines).

Import the resulting .dxf into an Onshape sketch (right-click a plane ->
"Import DXF/DWG", or File > Import), then extrude / fillet as normal.

Design goals: lightweight, one file, only trusted OSS libs (OpenCV, NumPy,
SciPy, ezdxf). "Smart simplify" = Douglas-Peucker point reduction plus an
optional smooth B-spline refit so you get as few, cleanest curves as possible.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import cv2
import ezdxf
import numpy as np


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class Options:
    # Binarization
    invert: bool = False          # treat dark shapes on light bg (default) vs light on dark
    threshold: int = -1           # fixed 0-255 threshold; -1 => Otsu (auto)
    canny: bool = False           # use Canny edges instead of filled-region contours
    blur: int = 3                 # gaussian blur kernel (odd, 0=off) to de-noise before edges

    # Contour selection
    min_area: float = 25.0        # drop contours smaller than this (px^2) as noise
    external_only: bool = False   # keep only outer contours (ignore holes)

    # Geometry inference (the "best fit" vectorizer: lines + arcs + splines)
    fit: bool = True              # infer straight lines / circular arcs / splines
    tol: float = 2.0              # max deviation (px) allowed when fitting a primitive
    depixel: float = 1.2          # gaussian de-jag applied to the raw contour before fitting
    corner_angle: float = 32.0    # a turn sharper than this (deg) starts a new segment
    weld: float = 1.5             # snap endpoints within this many px so shapes close (0=off)
    centerline: bool = False      # trace stroke skeletons (single path) instead of outlines
    fillet: float = 0.0           # round sharp line-line corners with this radius (px, 0=off)

    # Legacy simplify path (used only when fit=False)
    epsilon: float = 0.0015       # Douglas-Peucker tolerance as a fraction of the contour perimeter
    smooth: float = 0.0           # >0 => de-pixelate the raw contour before simplifying
    resample: int = 0             # unused (kept for CLI back-compat)

    # Output geometry
    as_polyline: bool = False     # write straight-segment LWPOLYLINE instead of SPLINE

    # Scale / units / orientation
    units: str = "mm"             # output unit label (mm, cm, in, m, px)
    scale_x: float = 0.0          # output units per pixel in X; 0 => derive from width_mm or 1.0
    scale_y: float = 0.0          # output units per pixel in Y; 0 => match scale_x (locked aspect)
    rotate: float = 0.0           # rotate the output this many degrees (CCW)
    width_mm: float = 0.0         # legacy: scale whole drawing to this width in mm
    flip_y: bool = True           # image Y grows down; CAD Y grows up -> flip by default

    # Extra construction geometry (own DXF layers)
    export_bbox: bool = False         # add the bounding-box rectangle
    export_centerlines: bool = False  # add horizontal+vertical center lines
    guide_ref: str = "geometry"       # guides framed on "geometry" bounds or "image" frame


# --------------------------------------------------------------------------- #
# Image -> contours
# --------------------------------------------------------------------------- #
def load_binary(path: str, opt: Options) -> np.ndarray:
    """Load image as a clean binary mask (foreground = 255)."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit(f"error: could not read image '{path}'")

    # Composite transparency onto white so alpha-cut PNGs work as expected.
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        img = (rgb * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)

    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    if opt.blur and opt.blur >= 3:
        k = opt.blur if opt.blur % 2 == 1 else opt.blur + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    if opt.canny:
        # Auto Canny thresholds from the median (Otsu-like heuristic).
        med = float(np.median(gray))
        lo = int(max(0, 0.66 * med))
        hi = int(min(255, 1.33 * med))
        edges = cv2.Canny(gray, lo, hi)
        # Close 1px gaps so contours connect.
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        return edges

    if opt.threshold >= 0:
        _, binimg = cv2.threshold(gray, opt.threshold, 255, cv2.THRESH_BINARY)
    else:
        _, binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # By convention we want the *shape* to be white (255). Otsu makes the
    # brighter region white; for dark-shape-on-light-bg that's the background,
    # so invert unless the user says otherwise.
    if not opt.invert:
        binimg = cv2.bitwise_not(binimg)
    return binimg


def find_contours(mask: np.ndarray, opt: Options) -> list[np.ndarray]:
    mode = cv2.RETR_EXTERNAL if opt.external_only else cv2.RETR_CCOMP
    # CHAIN_APPROX_NONE keeps every boundary pixel; the fitter needs the dense
    # path (a collapsed 4-point rectangle would spuriously fit a circle).
    contours, _ = cv2.findContours(mask, mode, cv2.CHAIN_APPROX_NONE)
    kept = [c for c in contours if cv2.contourArea(c) >= opt.min_area or (opt.canny and cv2.arcLength(c, False) >= 10)]
    return kept


# --------------------------------------------------------------------------- #
# Legacy simplify (fit=False): plain point reduction, optional de-pixel
# --------------------------------------------------------------------------- #
def simplify_contour(c: np.ndarray, opt: Options) -> np.ndarray:
    """Return an (N,2) float array of simplified points (polyline vertices)."""
    pts = c.reshape(-1, 2).astype(np.float64)
    closed = not opt.canny
    if opt.smooth > 0 and len(pts) >= 5:
        pts = _depixel(pts, closed, opt.smooth)   # de-jag BEFORE reducing points
    peri = cv2.arcLength(pts.astype(np.float32).reshape(-1, 1, 2), closed)
    eps = max(opt.epsilon * peri, 0.5)
    approx = cv2.approxPolyDP(
        pts.astype(np.float32).reshape(-1, 1, 2), eps, closed).reshape(-1, 2)
    return approx.astype(np.float64)


# --------------------------------------------------------------------------- #
# Geometry inference: fit a clean mix of LINES + ARCS + SPLINES to each contour.
#
# A raster edge is a staircase of 1px steps. We first low-pass the contour to
# recover the underlying smooth path (so straight edges stop looking jagged),
# then split it at genuine corners, then greedily fit the simplest primitive
# that stays within `tol` px of each piece: a straight line, else a circular
# arc, else (recursively splitting) a spline for truly freeform runs.
# --------------------------------------------------------------------------- #
def _depixel(pts: np.ndarray, closed: bool, sigma: float) -> np.ndarray:
    """Gaussian low-pass along the contour to undo pixel stair-stepping."""
    from scipy.ndimage import gaussian_filter1d
    sig = max(0.3, min(sigma, len(pts) / 6.0))   # never over-smooth tiny loops
    mode = "wrap" if closed else "nearest"
    x = gaussian_filter1d(pts[:, 0], sig, mode=mode)
    y = gaussian_filter1d(pts[:, 1], sig, mode=mode)
    return np.column_stack([x, y])


def _detect_corners(pts: np.ndarray, closed: bool, angle_deg: float, k: int = 4) -> list[int]:
    """Indices where the path turns sharper than angle_deg (local maxima)."""
    n = len(pts)
    if n < 2 * k + 1:
        return [] if closed else [0, n - 1]
    thr = np.radians(angle_deg)
    turn = np.zeros(n)
    rng = range(n) if closed else range(k, n - k)
    for i in rng:
        a = pts[(i - k) % n]; b = pts[i]; c = pts[(i + k) % n]
        v1 = b - a; v2 = c - b
        n1 = np.hypot(*v1); n2 = np.hypot(*v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cosang = np.clip((v1 @ v2) / (n1 * n2), -1.0, 1.0)
        turn[i] = np.arccos(cosang)
    # keep sharp turns that are local maxima within +/-k
    corners = []
    for i in rng:
        if turn[i] < thr:
            continue
        lo = [turn[(i + d) % n] for d in range(-k, k + 1)]
        if turn[i] >= max(lo) - 1e-9:
            corners.append(i)
    if not closed:
        corners = sorted(set([0, n - 1] + corners))
    return corners


def _fit_line(seg: np.ndarray):
    """Straight line through the segment's *endpoints*; returns (p0, p1, max_dev).

    We measure deviation from the chord we actually draw (endpoints seg[0]->seg[-1]),
    not a floating best-fit line, so the emitted endpoints are exactly the shared
    junction points and adjacent primitives connect with no gap.
    """
    p0, p1 = seg[0], seg[-1]
    chord = p1 - p0
    L = float(np.hypot(*chord))
    if L < 1e-6:
        dev = float(np.linalg.norm(seg - p0, axis=1).max())
    else:
        v = seg - p0
        dev = float(np.abs(chord[0] * v[:, 1] - chord[1] * v[:, 0]).max() / L)
    return p0.copy(), p1.copy(), dev


def _circle_from_3(a: np.ndarray, b: np.ndarray, c: np.ndarray):
    """Circumcircle through 3 points; returns (center, radius) or None if collinear."""
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay; b2 = bx * bx + by * by; c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    center = np.array([ux, uy])
    return center, float(np.hypot(*(center - a)))


def _fit_circle(seg: np.ndarray):
    """Algebraic (Kasa) circle fit; returns (center, radius, max_dev)."""
    x, y = seg[:, 0], seg[:, 1]
    A = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b = x * x + y * y
    (cx, cy, cc), *_ = np.linalg.lstsq(A, b, rcond=None)
    r = float(np.sqrt(max(cc + cx * cx + cy * cy, 1e-9)))
    dev = float(np.abs(np.hypot(x - cx, y - cy) - r).max())
    return np.array([cx, cy]), r, dev


def _fit_segment(seg: np.ndarray, tol: float, depth: int = 0) -> list[dict]:
    """Fit the simplest primitive(s) within tol to an open run of points."""
    if len(seg) < 2:
        return []
    if len(seg) == 2:
        return [{"kind": "line", "pts": seg.copy()}]

    p0, p1, ldev = _fit_line(seg)
    if ldev <= tol:
        return [{"kind": "line", "pts": np.array([p0, p1])}]

    # Arcs are gold for CAD (real radii to fillet against) - try before splines.
    if len(seg) >= 5:
        center, r, adev = _fit_circle(seg)
        span = np.hypot(*(seg[0] - seg[-1]))
        if adev <= tol and 1.0 < r < 50.0 * max(span, 1.0):
            # Store the 3 defining points; the emitted arc is the circle *through*
            # them, so it lands exactly on the shared endpoints (no gap to neighbors).
            return [{"kind": "arc", "p0": seg[0].copy(),
                     "pm": seg[len(seg) // 2].copy(), "p1": seg[-1].copy()}]

    # Neither line nor arc: split at the point furthest from the chord and recurse.
    if depth < 12 and len(seg) >= 6:
        chord = seg[-1] - seg[0]
        cl = np.hypot(*chord)
        if cl > 1e-6:
            v = seg - seg[0]
            d = np.abs(chord[0] * v[:, 1] - chord[1] * v[:, 0]) / cl   # 2-D cross magnitude
        else:
            d = np.linalg.norm(seg - seg[0], axis=1)
        j = int(np.argmax(d))
        if 0 < j < len(seg) - 1:
            return _fit_segment(seg[:j + 1], tol, depth + 1) + _fit_segment(seg[j:], tol, depth + 1)

    return [{"kind": "spline", "pts": seg.copy()}]   # genuinely freeform


def _vectorize_path(pts: np.ndarray, closed: bool, opt: Options) -> list[dict]:
    """Fit line/arc/circle/spline primitives to one ordered path of points."""
    pts = np.asarray(pts, dtype=np.float64)
    if opt.depixel > 0 and len(pts) >= 5:
        pts = _depixel(pts, closed, opt.depixel)
    if len(pts) < 3:
        return [{"kind": "line", "pts": pts}] if len(pts) == 2 else []

    # A clean closed loop with no corners is often a whole circle.
    corners = _detect_corners(pts, closed, opt.corner_angle)
    if closed and len(corners) < 2:
        center, r, dev = _fit_circle(pts)
        if dev <= opt.tol and r > 1.0:
            return [{"kind": "circle", "center": center, "r": r}]
        # Not a circle: cut at the real corner if we found one (e.g. a teardrop
        # tip), otherwise open the loop at the arbitrary tracing seam.
        if not corners:
            corners = [0]

    prims: list[dict] = []
    if closed:
        cs = sorted(set(corners))
        for a, b in zip(cs, cs[1:] + [cs[0] + len(pts)]):
            idx = np.arange(a, b + 1) % len(pts)
            prims += _fit_segment(pts[idx], opt.tol)
    else:
        cs = sorted(set(corners) | {0, len(pts) - 1})
        for a, b in zip(cs, cs[1:]):
            prims += _fit_segment(pts[a:b + 1], opt.tol)
    return prims


def vectorize_contour(c: np.ndarray, opt: Options) -> list[dict]:
    """Turn one raw OpenCV contour into a list of line/arc/circle/spline primitives."""
    return _vectorize_path(c.reshape(-1, 2), not opt.canny, opt)


# --------------------------------------------------------------------------- #
# Connectivity: weld near-coincident endpoints so profiles close up cleanly.
# --------------------------------------------------------------------------- #
def _endpoint_slots(prim: dict) -> list[tuple]:
    """(container, key) pairs for a primitive's two endpoints; both support c[k]=v."""
    k = prim["kind"]
    if k == "circle":
        return []
    if k == "arc":
        return [(prim, "p0"), (prim, "p1")]
    return [(prim["pts"], 0), (prim["pts"], -1)]   # line / spline arrays


def weld_endpoints(prims: list[dict], tol: float) -> int:
    """Snap endpoints within `tol` px to a shared point (union-find over a grid).

    This is the "closed-shape" pass: it fuses the seams between adjacent
    primitives and closes gaps where curves were meant to meet. Returns the
    number of distinct weld points created. Mutates primitives in place.
    """
    if tol <= 0:
        return 0
    slots = [s for p in prims for s in _endpoint_slots(p)]
    if not slots:
        return 0
    pts = np.array([np.asarray(c[k], dtype=float) for c, k in slots])
    n = len(pts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    inv = 1.0 / tol
    buckets: dict[tuple, list[int]] = {}
    for i, (x, y) in enumerate(pts):
        buckets.setdefault((int(np.floor(x * inv)), int(np.floor(y * inv))), []).append(i)
    t2 = tol * tol
    for i, (x, y) in enumerate(pts):
        bx, by = int(np.floor(x * inv)), int(np.floor(y * inv))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((bx + dx, by + dy), ()):
                    if j > i:
                        dxy = pts[i] - pts[j]
                        if dxy @ dxy <= t2:
                            parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    welds = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        welds += 1
        centroid = pts[members].mean(axis=0)
        for m in members:
            c, k = slots[m]
            c[k] = centroid
    return welds


def vectorize_all(contours: list[np.ndarray], opt: Options) -> list[list[dict]]:
    """Vectorize every contour, then weld endpoints across the whole drawing."""
    items = [vectorize_contour(c, opt) for c in contours]
    if opt.weld > 0:
        weld_endpoints([p for lst in items for p in lst], opt.weld)
    return items


# --------------------------------------------------------------------------- #
# Centerline: skeletonize strokes and trace the 1px medial paths, so a drawn
# line/letter becomes a single editable curve instead of a double outline.
# --------------------------------------------------------------------------- #
_NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def skeleton_paths(mask: np.ndarray, prune: float = 5.0) -> list[tuple]:
    """Return [(points Nx2, closed)] tracing the medial skeleton of `mask`."""
    from skimage.morphology import skeletonize
    sk = skeletonize(mask > 0)
    ys, xs = np.nonzero(sk)
    px = set(zip(map(int, xs), map(int, ys)))            # (x, y) pixels
    if not px:
        return []

    def nbrs(p):
        x, y = p
        return [(x + dx, y + dy) for dx, dy in _NB8 if (x + dx, y + dy) in px]

    deg = {p: len(nbrs(p)) for p in px}
    nodes = {p for p in px if deg[p] != 2}               # endpoints + junctions
    paths: list[tuple] = []
    seen_dir: set = set()

    def walk(a, b):
        path = [a, b]
        prev, cur = a, b
        while cur not in nodes:
            nxt = [q for q in nbrs(cur) if q != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur)
        return path

    for node in nodes:
        for nb in nbrs(node):
            if (node, nb) in seen_dir:
                continue
            path = walk(node, nb)
            seen_dir.add((node, nb))
            seen_dir.add((path[-1], path[-2]))            # its reverse
            paths.append((np.array(path, dtype=float), False))

    # Isolated loops have no nodes; walk what's left.
    used = {tuple(map(int, p)) for arr, _ in paths for p in arr}
    for start in px:
        if start in used or deg[start] != 2:
            continue
        loop = [start]; prev, cur = start, nbrs(start)[0]
        while cur != start and cur not in used:
            loop.append(cur); used.add(cur)
            nxt = [q for q in nbrs(cur) if q != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        used.add(start)
        if len(loop) >= 4:
            paths.append((np.array(loop, dtype=float), True))

    # Drop tiny spur branches (skeletonization noise).
    out = []
    for arr, closed in paths:
        length = np.hypot(*(arr[1:] - arr[:-1]).T).sum() if len(arr) > 1 else 0.0
        if length >= prune or closed:
            out.append((arr, closed))
    return out


def vectorize_centerline(mask: np.ndarray, opt: Options) -> list[list[dict]]:
    """Trace stroke skeletons and fit primitives to each medial path."""
    items = []
    for pts, closed in skeleton_paths(mask, prune=max(4.0, opt.tol * 2)):
        prims = _vectorize_path(pts, closed, opt)
        if prims:
            items.append(prims)
    if opt.weld > 0:
        weld_endpoints([p for lst in items for p in lst], opt.weld)
    return items


def build_items(mask: np.ndarray, opt: Options) -> list[list[dict]]:
    """One entry point: centerline / fitted-outline / legacy, per `opt`."""
    if opt.centerline:
        items = vectorize_centerline(mask, opt)
    elif opt.fit:
        items = vectorize_all(find_contours(mask, opt), opt)
    else:
        return [simplify_contour(c, opt) for c in find_contours(mask, opt)]
    if opt.fillet > 0:
        items = [fillet_path(prims, opt.fillet) for prims in items]
    return items


# --------------------------------------------------------------------------- #
# Fillet: round sharp line->line corners with a tangent arc of radius r.
# --------------------------------------------------------------------------- #
def fillet_path(prims: list[dict], radius: float) -> list[dict]:
    """Insert tangent fillet arcs at sharp corners where two straight lines meet."""
    n = len(prims)
    if radius <= 0 or n < 2:
        return prims
    lines = [p for p in prims if p["kind"] == "line"]
    if len(lines) < 2:
        return prims

    ln = [np.asarray(p["pts"], float) if p["kind"] in ("line", "spline") else None for p in prims]
    trimA: dict[int, np.ndarray] = {}   # new start for line i
    trimB: dict[int, np.ndarray] = {}   # new end for line i
    arc_after: dict[int, dict] = {}     # fillet arc to insert after prim i
    closed = _chain_is_closed(prims)
    last = n if closed else n - 1

    for j in range(last):
        a, b = prims[j], prims[(j + 1) % n]
        if a["kind"] != "line" or b["kind"] != "line":
            continue
        A0, A1 = ln[j][0], ln[j][-1]
        B0, B1 = ln[(j + 1) % n][0], ln[(j + 1) % n][-1]
        P = 0.5 * (A1 + B0)                          # shared corner (welded ~ equal)
        va, vb = A0 - P, B1 - P                       # directions away from the corner
        la, lb = np.hypot(*va), np.hypot(*vb)
        if la < 1e-3 or lb < 1e-3:
            continue
        va, vb = va / la, vb / lb
        cosang = float(np.clip(va @ vb, -1.0, 1.0))
        alpha = np.arccos(cosang)                     # interior angle at the corner
        if alpha < 0.09 or alpha > 3.05:              # ~5deg spike or ~175deg (straight)
            continue
        t = radius / np.tan(alpha / 2.0)
        t = min(t, 0.45 * la, 0.45 * lb)
        if t < 0.5:
            continue
        r_eff = t * np.tan(alpha / 2.0)
        pa, pb = P + va * t, P + vb * t               # tangent (trim) points
        bis = va + vb
        bl = np.hypot(*bis)
        if bl < 1e-6:
            continue
        center = P + (bis / bl) * (r_eff / np.sin(alpha / 2.0))
        midv = 0.5 * (pa + pb) - center
        mv = np.hypot(*midv)
        pm = center + (midv / mv) * r_eff if mv > 1e-9 else 0.5 * (pa + pb)
        trimB[j] = pa
        trimA[(j + 1) % n] = pb
        arc_after[j] = {"kind": "arc", "p0": pa.copy(), "pm": pm, "p1": pb.copy()}

    out: list[dict] = []
    for i, p in enumerate(prims):
        if p["kind"] == "line" and (i in trimA or i in trimB):
            q = dict(p)
            q["pts"] = np.array([trimA.get(i, ln[i][0]), trimB.get(i, ln[i][-1])])
            out.append(q)
        else:
            out.append(p)
        if i in arc_after:
            out.append(arc_after[i])
    return out


def _chain_is_closed(prims: list[dict], tol: float = 1e-6) -> bool:
    """True if the primitive chain's first and last endpoints coincide."""
    if not prims:
        return False
    a, b = primitive_endpoints(prims[0]), primitive_endpoints(prims[-1])
    if len(a) == 0 or len(b) == 0:                     # a full circle stands alone
        return len(prims) == 1
    return bool(np.hypot(*(a[0] - b[-1])) <= tol)


def geometry_bounds(items: list, is_primitive: bool) -> tuple | None:
    """(minx, miny, maxx, maxy) over all geometry in image coords, or None."""
    pts = []
    if is_primitive:
        for lst in items:
            for p in lst:
                pts.append(primitive_points(p))
    else:
        pts = [a for a in items if len(a)]
    if not pts:
        return None
    allp = np.vstack(pts)
    return float(allp[:, 0].min()), float(allp[:, 1].min()), \
        float(allp[:, 0].max()), float(allp[:, 1].max())


# --------------------------------------------------------------------------- #
# Auto-adjust: inspect the image and pick sensible settings automatically.
# --------------------------------------------------------------------------- #
def _read_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit(f"error: could not read image '{path}'")
    if img.ndim == 3 and img.shape[2] == 4:            # composite alpha over white
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * a + 255.0 * (1.0 - a)).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def auto_adjust(path: str) -> dict:
    """Analyze the image and return suggested settings for a clean CAD result."""
    gray = _read_gray(path)
    h, w = gray.shape[:2]
    diag = float(np.hypot(h, w))

    # 1) Foreground polarity: dark border => light shape on dark bg => invert.
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    invert = bool(np.median(border) < 110)

    # 2) Binarize the way load_binary will, to measure the actual shape.
    _, b = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if not invert:
        b = cv2.bitwise_not(b)

    # 3) Resolution-scaled tolerances (bigger images tolerate more px slack).
    tol = float(np.clip(1.2 + diag / 1000.0, 1.2, 5.0))
    depixel = float(np.clip(0.8 + diag / 2200.0, 0.8, 3.0))
    weld = float(np.clip(tol * 0.75, 1.0, 3.5))

    # 4) Speck cutoff from the contour-area distribution.
    cnts, _ = cv2.findContours(b, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    areas = sorted(cv2.contourArea(c) for c in cnts)
    total = float(h * w)
    min_area = 15.0
    if len(areas) > 6:
        tiny = [a for a in areas if a < 0.001 * total]
        if len(tiny) > 0.3 * len(areas):
            min_area = max(min_area, min(max(tiny), 0.002 * total))
    min_area = float(np.clip(min_area, 8.0, 1000.0))

    # 5) Stroke-ness: thin, low-fill artwork reads better as centerlines.
    fg_frac = float((b > 0).mean())
    dist = cv2.distanceTransform(b, cv2.DIST_L2, 5)
    half_w = float(np.median(dist[b > 0])) if (b > 0).any() else 0.0
    centerline = bool(fg_frac < 0.18 and 0 < half_w < 0.02 * diag)

    return {"invert": invert, "tol": round(tol, 2), "depixel": round(depixel, 2),
            "weld": round(weld, 2), "min_area": round(min_area), "centerline": centerline}


# --------------------------------------------------------------------------- #
# Sampling primitives back to points (for preview / polyline fallback)
# --------------------------------------------------------------------------- #
def _arc_angles(center, p0, pm, p1, n: int = 40):
    """CCW/CW angle sweep from p0 to p1 that passes through the midpoint pm."""
    a0 = np.arctan2(*(p0 - center)[::-1])
    a1 = np.arctan2(*(p1 - center)[::-1])
    am = np.arctan2(*(pm - center)[::-1])
    tau = 2 * np.pi
    ccw = (a1 - a0) % tau
    am_rel = (am - a0) % tau
    if am_rel <= ccw:            # midpoint lies on the CCW arc
        return a0 + np.linspace(0, ccw, n), a0, a1
    return a0 - np.linspace(0, tau - ccw, n), a1, a0   # otherwise sweep CW


def primitive_points(prim: dict) -> np.ndarray:
    """Sample a primitive to an (N,2) polyline in image coords."""
    k = prim["kind"]
    if k in ("line", "spline"):
        pts = prim["pts"]
        if k == "spline" and len(pts) >= 4:
            return _spline_samples(pts)
        return pts
    if k == "circle":
        th = np.linspace(0, 2 * np.pi, 96)
        return prim["center"] + prim["r"] * np.column_stack([np.cos(th), np.sin(th)])
    # arc: geometry is the circle through the (possibly welded) 3 points
    fit = _circle_from_3(prim["p0"], prim["pm"], prim["p1"])
    if fit is None:
        return np.array([prim["p0"], prim["pm"], prim["p1"]])
    center, r = fit
    th, _, _ = _arc_angles(center, prim["p0"], prim["pm"], prim["p1"])
    return center + r * np.column_stack([np.cos(th), np.sin(th)])


def _spline_samples(pts: np.ndarray, per_seg: int = 10) -> np.ndarray:
    from scipy import interpolate
    try:
        tck, _ = interpolate.splprep([pts[:, 0], pts[:, 1]], s=0, k=min(3, len(pts) - 1))
    except Exception:
        return pts
    u = np.linspace(0, 1, max(len(pts) * per_seg, 24))
    xs, ys = interpolate.splev(u, tck)
    return np.column_stack([xs, ys])


def primitive_endpoints(prim: dict) -> np.ndarray:
    """The two junction points of a primitive (empty for a full circle)."""
    if prim["kind"] == "circle":
        return np.empty((0, 2))
    if prim["kind"] == "arc":
        return np.array([prim["p0"], prim["p1"]])
    return prim["pts"][[0, -1]]


# --------------------------------------------------------------------------- #
# DXF output
# --------------------------------------------------------------------------- #
_DXF_UNITS = {"mm": 4, "cm": 5, "m": 6, "in": 1, "px": 0}


def resolve_scale(opt: Options, img_w: int) -> tuple[float, float]:
    """Output units-per-pixel in (x, y). scale_x/scale_y win; else width_mm; else 1."""
    sx = opt.scale_x if opt.scale_x > 0 else (
        opt.width_mm / img_w if opt.width_mm > 0 and img_w > 0 else 1.0)
    sy = opt.scale_y if opt.scale_y > 0 else sx
    return sx, sy


def make_transform(opt: Options, img_h: int, img_w: int):
    """Return a point-transform closure: image px -> output coords (flip/scale/rotate)."""
    sx, sy = resolve_scale(opt, img_w)
    ang = np.radians(opt.rotate)
    ca, sa = np.cos(ang), np.sin(ang)
    pivot = np.array([img_w * sx / 2.0, img_h * sy / 2.0])

    def tf(p) -> np.ndarray:
        q = np.asarray(p, dtype=float).copy()
        if opt.flip_y:
            q[..., 1] = img_h - q[..., 1]
        q[..., 0] *= sx
        q[..., 1] *= sy
        if ang:
            d = q - pivot
            x = d[..., 0] * ca - d[..., 1] * sa
            y = d[..., 0] * sa + d[..., 1] * ca
            q = np.stack([x, y], axis=-1) + pivot
        return q

    return tf, sx, sy


def write_dxf(items: list, out: str, opt: Options, img_h: int, img_w: int) -> dict:
    """Write a DXF. `items` is a list of primitive-lists or (legacy) point-arrays.

    Returns a tally, e.g. {"line": 8, "arc": 3, "spline": 1, "total": 12}.
    """
    tf, sx, sy = make_transform(opt, img_h, img_w)
    uniform = abs(sx - sy) <= 1e-6 * max(sx, sy, 1.0)
    is_prim = opt.fit or opt.centerline

    doc = ezdxf.new("R2010", setup=True)   # setup=True loads DASHED etc. linetypes
    doc.units = _DXF_UNITS.get(opt.units, 4)
    msp = doc.modelspace()

    tally: dict[str, int] = {}

    def bump(kind: str):
        tally[kind] = tally.get(kind, 0) + 1

    if is_prim:
        for prims in items:
            for pr in prims:
                _emit_primitive(msp, pr, tf, sx, uniform, bump)
    else:
        for pts in items:
            if len(pts) < 2:
                continue
            pts = tf(pts)
            closed = not opt.canny
            if opt.as_polyline:
                msp.add_lwpolyline(pts[:, :2], close=closed); bump("polyline")
            else:
                fit = pts.tolist()
                if closed:
                    fit.append(fit[0])
                msp.add_spline(fit_points=fit); bump("spline")

    if opt.export_bbox or opt.export_centerlines:
        _emit_guides(doc, msp, output_bounds(items, is_prim, tf, opt, img_w, img_h), opt, bump)

    doc.saveas(out)
    tally["total"] = sum(tally.values())
    return tally


def _emit_primitive(msp, pr: dict, tf, sx: float, uniform: bool, bump, layer: str = "0") -> None:
    da = {"layer": layer}
    k = pr["kind"]
    if k == "line":
        p = tf(pr["pts"])
        msp.add_line(p[0], p[-1], dxfattribs=da); bump("line")
    elif k == "spline":
        pts = tf(pr["pts"])
        if len(pts) >= 3:
            msp.add_spline(fit_points=pts.tolist(), dxfattribs=da); bump("spline")
        elif len(pts) >= 2:
            msp.add_line(pts[0], pts[-1], dxfattribs=da); bump("line")
    elif k == "circle":
        if uniform:
            msp.add_circle(tf(pr["center"]), pr["r"] * sx, dxfattribs=da); bump("circle")
        else:                                   # non-uniform scale -> ellipse-ish, sample it
            msp.add_spline(fit_points=tf(primitive_points(pr)).tolist(), dxfattribs=da); bump("spline")
    elif k == "arc":
        if uniform:
            p0, pm, p1 = tf(pr["p0"]), tf(pr["pm"]), tf(pr["p1"])
            fit = _circle_from_3(p0, pm, p1)     # recompute in transformed space
            if fit is None:
                msp.add_line(p0, p1, dxfattribs=da); bump("line"); return
            c, r = fit
            _, a0, a1 = _arc_angles(c, p0, pm, p1)
            msp.add_arc(c, r, np.degrees(a0), np.degrees(a1), dxfattribs=da); bump("arc")
        else:
            msp.add_spline(fit_points=tf(primitive_points(pr)).tolist(), dxfattribs=da); bump("spline")


def output_bounds(items, is_prim, tf, opt: Options, img_w, img_h):
    """Axis-aligned bounds of the geometry *after* the transform (recalculated,
    so rotation shrinks/grows the box rather than tilting it)."""
    if opt.guide_ref == "image":
        c = tf(np.array([[0, 0], [img_w, 0], [img_w, img_h], [0, img_h]], float))
    else:
        pts = ([tf(primitive_points(p)) for lst in items for p in lst] if is_prim
               else [tf(a) for a in items if len(a)])
        if not pts:
            return None
        c = np.vstack(pts)
    return float(c[:, 0].min()), float(c[:, 1].min()), float(c[:, 0].max()), float(c[:, 1].max())


def _emit_guides(doc, msp, bounds, opt: Options, bump) -> None:
    """Add an axis-aligned bounding box and/or center cross-hairs (own dashed layers).

    `bounds` is already in output coordinates, so the box stays axis-aligned to the
    rotated result instead of tilting with it.
    """
    if bounds is None:
        return
    x0, y0, x1, y1 = bounds
    if opt.export_bbox:
        doc.layers.add("BBOX", color=8, linetype="DASHED")
        c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        for a, b in zip(c[:-1], c[1:]):
            msp.add_line(a, b, dxfattribs={"layer": "BBOX"}); bump("bbox")
    if opt.export_centerlines:
        doc.layers.add("CENTERLINES", color=1, linetype="CENTER")
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        msp.add_line((cx, y0), (cx, y1), dxfattribs={"layer": "CENTERLINES"}); bump("centerline")
        msp.add_line((x0, cy), (x1, cy), dxfattribs={"layer": "CENTERLINES"}); bump("centerline")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="img2cad",
        description="Detect edges in a PNG and export a clean DXF for Onshape.",
    )
    p.add_argument("input", help="input image (png/jpg/...)")
    p.add_argument("-o", "--output", default=None, help="output .dxf (default: <input>.dxf)")

    g = p.add_argument_group("detection")
    g.add_argument("--canny", action="store_true", help="trace outline edges instead of filled shapes")
    g.add_argument("--invert", action="store_true", help="foreground is the LIGHT region, not the dark one")
    g.add_argument("--threshold", type=int, default=-1, help="fixed 0-255 threshold (default: auto/Otsu)")
    g.add_argument("--blur", type=int, default=3, help="gaussian blur kernel to de-noise (odd, 0=off)")
    g.add_argument("--min-area", type=float, default=25.0, help="drop specks smaller than this (px^2)")
    g.add_argument("--external-only", action="store_true", help="ignore interior holes")

    s = p.add_argument_group("geometry fit (lines + arcs + splines)")
    s.add_argument("--no-fit", action="store_true",
                   help="disable smart line/arc fitting; emit one spline per contour")
    s.add_argument("--tol", type=float, default=2.0,
                   help="max px a fitted line/arc may deviate (bigger=simpler geometry)")
    s.add_argument("--depixel", type=float, default=1.2,
                   help="de-jag strength applied before fitting (0=off)")
    s.add_argument("--corner-angle", type=float, default=32.0,
                   help="turn sharper than this (deg) becomes a corner")
    s.add_argument("--weld", type=float, default=1.5,
                   help="snap endpoints within this many px so shapes close (0=off)")
    s.add_argument("--centerline", action="store_true",
                   help="trace stroke skeletons (single path) instead of outlines")
    s.add_argument("--fillet", type=float, default=0.0,
                   help="round sharp line-line corners with this radius (px, 0=off)")
    s.add_argument("--polyline", action="store_true",
                   help="(with --no-fit) emit polylines instead of splines")
    s.add_argument("--epsilon", type=float, default=0.0015,
                   help="(with --no-fit) point-reduction tolerance as fraction of perimeter")
    s.add_argument("--smooth", type=float, default=0.0,
                   help="(with --no-fit) de-pixelate before reducing points")
    s.add_argument("--resample", type=int, default=0, help=argparse.SUPPRESS)

    o = p.add_argument_group("scale / units / output")
    o.add_argument("--units", default="mm", choices=["mm", "cm", "m", "in", "px"],
                   help="output units (default mm)")
    o.add_argument("--width-mm", type=float, default=0.0, help="scale whole drawing to this width in mm")
    o.add_argument("--scale", type=float, default=0.0, help="output units per pixel (overrides --width-mm)")
    o.add_argument("--rotate", type=float, default=0.0, help="rotate output this many degrees (CCW)")
    o.add_argument("--export-bbox", action="store_true", help="add bounding-box rectangle (own layer)")
    o.add_argument("--export-centerlines", action="store_true", help="add center cross-hairs (own layer)")
    o.add_argument("--no-flip-y", action="store_true", help="keep image orientation (default flips Y for CAD)")
    return p


def options_from_args(a: argparse.Namespace) -> Options:
    return Options(
        invert=a.invert,
        threshold=a.threshold,
        canny=a.canny,
        blur=a.blur,
        min_area=a.min_area,
        external_only=a.external_only,
        fit=not a.no_fit,
        tol=a.tol,
        depixel=a.depixel,
        corner_angle=a.corner_angle,
        weld=a.weld,
        centerline=a.centerline,
        fillet=a.fillet,
        epsilon=a.epsilon,
        smooth=a.smooth,
        resample=a.resample,
        as_polyline=a.polyline,
        units=a.units,
        scale_x=a.scale,
        scale_y=a.scale,
        rotate=a.rotate,
        width_mm=a.width_mm,
        flip_y=not a.no_flip_y,
        export_bbox=a.export_bbox,
        export_centerlines=a.export_centerlines,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    opt = options_from_args(args)
    out = args.output or (args.input.rsplit(".", 1)[0] + ".dxf")

    mask = load_binary(args.input, opt)
    h, w = mask.shape[:2]
    items = build_items(mask, opt)
    if not any(items):
        print("nothing found - try --invert, --canny, or a lower --min-area", file=sys.stderr)
        return 2
    tally = write_dxf(items, out, opt, h, w)

    breakdown = ", ".join(f"{v} {k}" for k, v in tally.items() if k != "total")
    print(f"ok: {tally['total']} entities ({breakdown}) -> {out}")
    print(f"    import into Onshape: sketch a plane, right-click > Import DXF/DWG > pick {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
