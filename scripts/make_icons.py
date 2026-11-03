"""Tekent het app-icoon (groene tegel met witte auto) als SVG en als PNG/ICO in meerdere formaten.

Gebruik (vereist Pillow, alleen nodig om de iconen opnieuw te maken):  python scripts/make_icons.py static

Ontwerp op een raster van 64x64; de PNG's worden op 8x tekengrootte getekend en verkleind (anti-aliasing).
"""
import os
import sys
from PIL import Image, ImageDraw

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

GREEN = '#108e77'
DARK = '#0a5c4d'
WHITE = '#ffffff'
GLASS = '#bfe9dd'

# --- vormen op 64x64 raster ---
CABIN = [(19, 29), (24.5, 18.5), (39.5, 18.5), (46, 29)]
WIN_L = [(23, 28), (26.6, 21.5), (31, 21.5), (31, 28)]
WIN_R = [(33.5, 28), (33.5, 21.5), (38.4, 21.5), (42.6, 28)]
BODY = (9, 28, 55, 44, 6)        # x1, y1, x2, y2, radius
WHEELS = [(21, 44), (43, 44)]
WHEEL_R, HUB_R = 6.2, 2.4
LIGHT = (50.5, 33, 54, 36.5)      # koplamp


def svg(rounded=True):
    def pts(p):
        return ' '.join(f'{x},{y}' for x, y in p)
    x1, y1, x2, y2, r = BODY
    bg = (f'<rect width="64" height="64" rx="14" fill="{GREEN}"/>' if rounded
          else f'<rect width="64" height="64" fill="{GREEN}"/>')
    wheels = ''.join(f'<circle cx="{cx}" cy="{cy}" r="{WHEEL_R}" fill="{DARK}" stroke="{WHITE}" stroke-width="2.2"/>'
                     f'<circle cx="{cx}" cy="{cy}" r="{HUB_R}" fill="{WHITE}"/>' for cx, cy in WHEELS)
    lx1, ly1, lx2, ly2 = LIGHT
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{bg}'
            f'<g transform="translate(0,-3)"><polygon points="{pts(CABIN)}" fill="{WHITE}" stroke="{WHITE}" stroke-width="2" stroke-linejoin="round"/>'
            f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" rx="{r}" fill="{WHITE}"/>'
            f'<polygon points="{pts(WIN_L)}" fill="{GLASS}" stroke="{GLASS}" stroke-width="1" stroke-linejoin="round"/>'
            f'<polygon points="{pts(WIN_R)}" fill="{GLASS}" stroke="{GLASS}" stroke-width="1" stroke-linejoin="round"/>'
            f'<rect x="{lx1}" y="{ly1}" width="{lx2 - lx1}" height="{ly2 - ly1}" rx="1.2" fill="#f6c453"/>'
            f'{wheels}</g></svg>\n')


def png(size, rounded, pad=0.0):
    """pad: extra marge (fractie) rond de auto, voor 'maskable' Android-iconen."""
    S = size * 8
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 14 / 64, fill=GREEN)
    else:
        d.rectangle([0, 0, S, S], fill=GREEN)
    scale = S / 64 * (1 - 2 * pad)
    off = S * pad

    def P(x, y):
        return (off + x * scale, off + (y - 3) * scale)

    def poly(p, fill, outline=None, w=0):
        d.polygon([P(*q) for q in p], fill=fill)
        if outline:
            d.line([P(*q) for q in p + [p[0]]], fill=outline, width=int(w * scale), joint='curve')

    poly(CABIN, WHITE, WHITE, 2)
    x1, y1, x2, y2, r = BODY
    d.rounded_rectangle([*P(x1, y1), *P(x2, y2)], radius=r * scale, fill=WHITE)
    poly(WIN_L, GLASS, GLASS, 1)
    poly(WIN_R, GLASS, GLASS, 1)
    lx1, ly1, lx2, ly2 = LIGHT
    d.rounded_rectangle([*P(lx1, ly1), *P(lx2, ly2)], radius=1.2 * scale, fill='#f6c453')
    for cx, cy in WHEELS:
        R = (WHEEL_R + 1.1) * scale
        c = P(cx, cy)
        d.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], fill=WHITE)
        R = (WHEEL_R - 1.1) * scale
        d.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], fill=DARK)
        R = HUB_R * scale
        d.ellipse([c[0] - R, c[1] - R, c[0] + R, c[1] + R], fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


open(os.path.join(OUT, 'favicon.svg'), 'w', encoding='utf-8').write(svg())
png(180, rounded=False).convert('RGB').save(os.path.join(OUT, 'apple-touch-icon.png'), optimize=True)
png(192, rounded=False, pad=0.1).save(os.path.join(OUT, 'icon-192.png'), optimize=True)
png(512, rounded=False, pad=0.1).save(os.path.join(OUT, 'icon-512.png'), optimize=True)
ico = png(256, rounded=True)
ico.save(os.path.join(OUT, 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48)])

# voorbeeldplaat om het ontwerp te beoordelen: licht en donker tabblad, verschillende maten
sheet = Image.new('RGB', (760, 300), '#ffffff')
dark = Image.new('RGB', (380, 300), '#202124'); sheet.paste(dark, (380, 0))
x = 20
for s in (16, 32, 48, 96):
    im = png(s, True)
    sheet.paste(im, (x, 40), im); sheet.paste(im, (x + 380, 40), im)
    x += s + 20
big = png(180, False)
sheet.paste(big, (20, 110)); sheet.paste(png(120, True), (420, 150), png(120, True))
sheet.save(os.path.join(OUT, 'preview.png'))
print('ok')
