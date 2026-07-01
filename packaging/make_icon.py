"""Generate img2cad.ico — a small, on-brand icon (slate tile + teal spline)."""
import os

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 256                      # master size; ICO is built from downscales
SS = 4                       # supersample for smooth anti-aliasing

BG_OUTER = (11, 14, 19)      # near-black rim
BG = (23, 29, 39)            # slate tile
BORDER = (44, 54, 68)
TEAL = (45, 212, 191)
AMBER = (245, 179, 1)
NODE = (240, 245, 252)


def catmull(points, n=220):
    """Sample a smooth Catmull-Rom spline through the control points."""
    p = np.array(points, float)
    p = np.vstack([p[0], p, p[-1]])
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, n // (len(points) - 1)):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t +
                              (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
                              (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    return np.array(out)


def draw(scale):
    z = S * scale
    img = Image.new("RGBA", (z, z), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = z * 0.055
    r = z * 0.22
    d.rounded_rectangle([m, m, z - m, z - m], radius=r, fill=BG_OUTER)
    m2 = z * 0.085
    d.rounded_rectangle([m2, m2, z - m2, z - m2], radius=r * 0.85,
                        fill=BG, outline=BORDER, width=max(1, int(z * 0.006)))

    def sc(pts):
        return [(x * z, y * z) for x, y in pts]

    # amber straight segment (a fitted "line")
    line = sc([(0.24, 0.72), (0.5, 0.44)])
    d.line(line, fill=AMBER, width=max(2, int(z * 0.028)))

    # teal spline sweeping across the tile (a fitted "curve")
    curve = catmull(sc([(0.22, 0.34), (0.42, 0.60), (0.62, 0.30), (0.80, 0.66)]))
    d.line([tuple(p) for p in curve], fill=TEAL, width=max(2, int(z * 0.036)), joint="curve")

    # node dots at the fit points
    for (x, y) in sc([(0.22, 0.34), (0.62, 0.30), (0.80, 0.66), (0.24, 0.72), (0.5, 0.44)]):
        rr = z * 0.028
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=NODE, outline=BG_OUTER,
                  width=max(1, int(z * 0.008)))
    return img


def main():
    master = draw(SS).resize((S, S), Image.LANCZOS)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    out_ico = os.path.join(HERE, "img2cad.ico")
    icons[-1].save(out_ico, format="ICO", sizes=[(s, s) for s in sizes])
    master.save(os.path.join(HERE, "img2cad.png"))
    print("wrote", out_ico)


if __name__ == "__main__":
    main()
