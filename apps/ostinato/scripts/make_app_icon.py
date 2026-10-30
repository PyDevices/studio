"""Draw Ostinato's app icons (icon-192.png, icon-512.png).

    ~/gh/pydevices/.toolbox/bin/python scripts/make_app_icon.py

A loop (an ostinato is a figure that repeats) with four notes on it, in the
app's accent colour on its background.
"""

import math
import pathlib

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
BG = (14, 17, 22, 255)
ACCENT = (255, 122, 69, 255)
SOFT = (255, 122, 69, 110)


def draw(size):
    s = 4 * size                     # draw big, scale down for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)
    cx = cy = s / 2
    r = s * 0.30
    w = int(s * 0.045)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=SOFT, width=w)
    for k in range(4):
        a = -math.pi / 2 + k * math.pi / 2
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        nr = s * (0.085 if k == 0 else 0.065)
        d.ellipse([x - nr, y - nr, x + nr, y + nr], fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


for size in (192, 512):
    draw(size).save(ROOT / ("icon-%d.png" % size))
    print("icon-%d.png" % size)
