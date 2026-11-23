"""Draws nextime's app symbols (TIME-28 AC-2) from the values of the design system's mark "Blatt mit Weiter":
tile, fine rim, calendar sheet with its gradient, header line, the two rings, the arrow cut free by the same path.
Writes the PNGs into frontend/public/icons. Run: python scripts/app-icons.py (needs Pillow)"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / 'public' / 'icons'

LOOKS = {
    'light': {'tile': '#ffffff', 'border': '#4a6491', 'border_alpha': 0.35, 'gradient': ['#a9bcdc', '#4a6491', '#26344f'], 'arrow': '#3d5682'},
    'dark': {'tile': '#0e1118', 'border': '#9fb4d8', 'border_alpha': 0.45, 'gradient': ['#dde6f4', '#9fb4d8', '#5a76a6'], 'arrow': '#c3d2ea'},
}
SS = 4  # supersampling


def rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def gradient(size, unit, ox, oy, colors):
    """The linear gradient from (10,10) to (54,54) of the 64 grid, as an RGB image of `size`."""
    stops = [rgb(c) for c in colors]
    # The colour depends on x + y only: one long row, cropped for every line.
    row = Image.new('RGB', (2 * size, 1))
    px = row.load()
    for k in range(2 * size):
        t = (k / unit - 2 * ox - 20) / 88
        t = min(1.0, max(0.0, t))
        if t < 0.5:
            a, b, f = stops[0], stops[1], t / 0.5
        else:
            a, b, f = stops[1], stops[2], (t - 0.5) / 0.5
        px[k, 0] = tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3))
    img = Image.new('RGB', (size, size))
    for y in range(size):
        img.paste(row.crop((y, 0, y + size, 1)), (0, y))
    return img


def draw(size, look, kind):
    """kind: 'any' (rounded tile, transparent corners), 'full' (square filled with the tile's color: maskable and the
    Apple symbol, scaled by `scale` so the mark stays in the safe zone)."""
    big = size * SS
    scale = {'any': 1.0, 'maskable': 0.74, 'apple': 0.86}[kind]
    unit = big / 64 * scale
    ox = oy = (64 - 64 * scale) / 2 / scale  # offset in grid units so the mark sits in the middle
    colors = LOOKS[look]

    def P(x, y):
        return ((x + ox) * unit, (y + oy) * unit)

    canvas = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    tile = rgb(colors['tile'])
    if kind == 'any':
        d.rounded_rectangle([P(2, 2), P(62, 62)], radius=16 * unit, fill=tile + (255,))
        rim = Image.new('L', (big, big), 0)
        rd = ImageDraw.Draw(rim)
        w = 1.5 * unit
        rd.rounded_rectangle([P(2.75 - 0.75, 2.75 - 0.75), P(61.25 + 0.75, 61.25 + 0.75)], radius=(15.3 + 0.75) * unit, outline=255, width=max(1, round(w)))
        rim = rim.point(lambda v: round(v * colors['border_alpha']))
        canvas.paste(Image.new('RGBA', (big, big), rgb(colors['border']) + (255,)), (0, 0), rim)
    else:
        d.rectangle([0, 0, big, big], fill=tile + (255,))

    grad = gradient(big, unit, ox, oy, colors['gradient']).convert('RGBA')

    def stroke_mask(paint):
        mask = Image.new('L', (big, big), 0)
        paint(ImageDraw.Draw(mask))
        return mask

    def round_line(md, a, b, width):
        md.line([P(*a), P(*b)], fill=255, width=round(width * unit))
        r = width * unit / 2
        for x, y in (a, b):
            cx, cy = P(x, y)
            md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)

    # The sheet, 4.2 wide, centred on its path.
    w = 4.2
    sheet = stroke_mask(lambda md: md.rounded_rectangle([P(11 - w / 2, 16 - w / 2), P(43 + w / 2, 49 + w / 2)], radius=(6 + w / 2) * unit, outline=255, width=round(w * unit)))
    canvas.paste(grad, (0, 0), sheet)
    # The header line at 60 %.
    header = stroke_mask(lambda md: md.line([P(11, 26), P(43, 26)], fill=255, width=round(3.2 * unit)))
    canvas.paste(grad, (0, 0), header.point(lambda v: round(v * 0.6)))
    # The rings, round.
    rings = stroke_mask(lambda md: (round_line(md, (20, 11.5), (20, 19.5), 4), round_line(md, (34, 11.5), (34, 19.5), 4)))
    canvas.paste(grad, (0, 0), rings)

    def arrow(width):
        def paint(md):
            round_line(md, (40, 33), (52, 42), width)
            round_line(md, (52, 42), (40, 51), width)
        return stroke_mask(paint)

    canvas.paste(Image.new('RGBA', (big, big), tile + (255,)), (0, 0), arrow(11))
    canvas.paste(Image.new('RGBA', (big, big), rgb(colors['arrow']) + (255,)), (0, 0), arrow(5))
    if kind == 'any':
        # Keep the corners outside the tile clear.
        outside = stroke_mask(lambda md: md.rounded_rectangle([P(2, 2), P(62, 62)], radius=16 * unit, fill=255))
        alpha = ImageChops.multiply(canvas.getchannel('A'), outside)
        canvas.putalpha(alpha)
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for look in ('light', 'dark'):
        suffix = '' if look == 'light' else '-dark'
        for size in (192, 512):
            draw(size, look, 'any').save(OUT / f'icon-{size}{suffix}.png', optimize=True)
            draw(size, look, 'maskable').convert('RGB').save(OUT / f'maskable-{size}{suffix}.png', optimize=True)
        draw(180, look, 'apple').convert('RGB').save(OUT / f'apple-touch-icon{suffix}.png', optimize=True)
    print('written', sorted(p.name for p in OUT.iterdir()))


if __name__ == '__main__':
    main()
