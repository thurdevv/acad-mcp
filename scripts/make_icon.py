"""Generate the project icon (original artwork: a drafting set square and dimension line; no third-party logos)."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "mcpb" / "icon.png"


def make(size: int = 512) -> Image.Image:
    s = size / 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(96 * s), fill=(24, 46, 82, 255))
    # grid
    for k in range(64, 512, 64):
        d.line([(k * s, 40 * s), (k * s, 472 * s)], fill=(46, 74, 120, 255), width=max(1, int(2 * s)))
        d.line([(40 * s, k * s), (472 * s, k * s)], fill=(46, 74, 120, 255), width=max(1, int(2 * s)))
    # set square (right triangle with a cut-out)
    tri = [(110 * s, 400 * s), (110 * s, 120 * s), (390 * s, 400 * s)]
    d.polygon(tri, fill=(255, 196, 61, 255))
    hole = [(160 * s, 350 * s), (160 * s, 240 * s), (270 * s, 350 * s)]
    d.polygon(hole, fill=(24, 46, 82, 255))
    # dimension line with ticks
    y = 445 * s
    d.line([(110 * s, y), (390 * s, y)], fill=(236, 242, 250, 255), width=int(8 * s))
    for x in (110, 390):
        d.line([((x - 12) * s, y + 12 * s), ((x + 12) * s, y - 12 * s)], fill=(236, 242, 250, 255), width=int(8 * s))
    # cursor cross (the "AI hand")
    cx, cy = 360 * s, 150 * s
    d.ellipse([cx - 34 * s, cy - 34 * s, cx + 34 * s, cy + 34 * s], outline=(120, 220, 255, 255), width=int(8 * s))
    d.line([(cx - 60 * s, cy), (cx + 60 * s, cy)], fill=(120, 220, 255, 255), width=int(6 * s))
    d.line([(cx, cy - 60 * s), (cx, cy + 60 * s)], fill=(120, 220, 255, 255), width=int(6 * s))
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    make(512).save(OUT)
    print("icon:", OUT)
