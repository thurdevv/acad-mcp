"""Record docs/demo.gif: draw a small floor plan through the MCP tools, one screenshot per step.

Needs AutoCAD open (window not minimized). Works on a new drawing and closes it without saving.
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "demo.gif"
WIDTH = 960


async def main() -> None:
    from mcp import Client

    from acad_mcp.config import Config
    from acad_mcp.server import build

    frames: list[tuple[Image.Image, str]] = []
    async with Client(build(Config())) as c:
        async def call(tool, /, **args):
            r = await c.call_tool(tool, args)
            if r.is_error:
                raise RuntimeError(f"{tool}: {r.content[0].text}")
            return r.structured_content

        async def shot(caption: str, zoom: str = "extents"):
            r = await c.call_tool("get_view_image", {"zoom": zoom, "margin": 0.12, "max_width": WIDTH})
            png = next(x for x in r.content if x.type == "image")
            frames.append((Image.open(io.BytesIO(base64.b64decode(png.data))).convert("RGB"), caption))

        doc = (await call("new_drawing"))["created"]["name"]
        try:
            for name, color, lt, lw in (("WALL", 7, None, 0.5), ("AXIS", 3, "CENTER", 0.18), ("DIM", 2, None, 0.18),
                                        ("TEXT", 4, None, 0.25), ("FURNITURE", 8, None, 0.18)):
                await call("manage_layer", name=name, color=color, linetype=lt, lineweight=lw)
            await call("set_current", ltscale=40, text_size=180)

            b = await call("draw_batch", layer="WALL", items=[
                {"type": "rectangle", "corner1": [0, 0], "corner2": [8000, 6000]},
                {"type": "rectangle", "corner1": [150, 150], "corner2": [7850, 5850]},
            ])
            await shot('"Draw a 8 x 6 m apartment with 15 cm walls"')

            await call("draw_batch", layer="WALL", items=[
                {"type": "line", "start": [4425, 150], "end": [4425, 2600]},
                {"type": "line", "start": [4575, 150], "end": [4575, 2600]},
                {"type": "line", "start": [4425, 3500], "end": [4425, 5850]},
                {"type": "line", "start": [4575, 3500], "end": [4575, 5850]},
                {"type": "line", "start": [4425, 2600], "end": [4575, 2600]},
                {"type": "line", "start": [4425, 3500], "end": [4575, 3500]},
                {"type": "arc", "center": [4575, 2600], "radius": 900, "start_angle_deg": 0, "end_angle_deg": 90},
                {"type": "line", "start": [4575, 2600], "end": [5475, 2600]},
            ])
            await shot('"Add an inner wall with a 90 cm door"')

            await call("draw_batch", layer="AXIS", items=[
                {"type": "line", "start": [-600, 3000], "end": [8600, 3000]},
                {"type": "line", "start": [4500, -600], "end": [4500, 6600]},
            ])
            furn = await call("draw_batch", layer="FURNITURE", items=[
                {"type": "rectangle", "corner1": [5600, 3800], "corner2": [7400, 5800]},
                {"type": "rectangle", "corner1": [600, 600], "corner2": [2800, 1500]},
                {"type": "circle", "center": [2000, 4200], "radius": 600},
            ])
            await call("draw_batch", layer="TEXT", items=[
                {"type": "text", "text": "LIVING ROOM", "insertion": [2200, 3200], "height": 220, "justify": "center"},
                {"type": "text", "text": "BEDROOM", "insertion": [6200, 1600], "height": 220, "justify": "center"},
            ])
            await shot('"Axes, furniture and room names"')

            await call("draw_dimension", kind="horizontal", p1=[0, 0], p2=[8000, 0], text_position=[4000, -900],
                       layer="DIM", text_height=220, arrow_size=160)
            await call("draw_dimension", kind="vertical", p1=[8000, 0], p2=[8000, 6000], text_position=[8900, 3000],
                       layer="DIM", text_height=220, arrow_size=160)
            await call("draw_hatch", boundary_handles=[b["handles"][0]], inner_boundary_handles=[b["handles"][1]],
                       pattern="ANSI31", scale=30, layer="WALL", color=8)  # the walls: outer outline minus the room
            await call("draw_hatch", boundary_handles=[furn["handles"][0]], pattern="ANSI37", scale=40,
                       layer="FURNITURE", color=8)
            await call("quantities", group_by="layer")
            await shot('"Dimension it and hatch the walls and the bed"')

            await call("check_standards", standard_name="example")
            await shot('"Check it against the layer standard"  ->  1 call per step, 1 Ctrl+Z each')
        finally:
            await call("close_drawing", name=doc, discard_changes=True)

    # caption bar + assemble
    out = []
    font = ImageFont.load_default(size=22)
    for img, caption in frames:
        img = img.resize((WIDTH, int(img.height * WIDTH / img.width)))
        canvas = Image.new("RGB", (WIDTH, img.height + 48), (18, 26, 40))
        canvas.paste(img, (0, 48))
        ImageDraw.Draw(canvas).text((16, 12), caption, fill=(255, 210, 90), font=font)
        out.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=128))
    OUT.parent.mkdir(exist_ok=True)
    out[0].save(OUT, save_all=True, append_images=out[1:], duration=[1800] * (len(out) - 1) + [3500], loop=0,
                optimize=True)
    print("gif:", OUT, f"{OUT.stat().st_size // 1024} KB, {len(out)} frames")


if __name__ == "__main__":
    asyncio.run(main())
