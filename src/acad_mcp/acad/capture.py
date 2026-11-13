"""Image of the AutoCAD drawing area (PrintWindow; works even when the window is covered)."""

from __future__ import annotations

import ctypes
import io
import math
from typing import Any

import win32gui
import win32ui
from PIL import Image, ImageDraw, ImageFont

from ..i18n import t as tr
from . import commands
from .errors import AcadError
from .winutil import active_view_hwnd, is_minimized

PW_RENDERFULLCONTENT = 2


def capture_view_png(app: Any, max_width: int = 1600, crop_to_view: bool = True,
                     grid: dict[str, Any] | None = None) -> tuple[bytes, dict]:
    """grid = {"center": VIEWCTR, "view_height": VIEWSIZE} to overlay coordinates."""
    main = int(app.HWND)
    if is_minimized(main):
        if not commands.AUTO_RESTORE:
            raise AcadError(tr(
                "The AutoCAD window is minimized, so no image can be captured. "
                "Ask the user to restore it (it may stay behind other windows).",
                "A janela do AutoCAD está minimizada; não dá para capturar a imagem. "
                "Peça ao usuário para restaurar a janela (pode ficar atrás de outras).",
            ))
        commands.restore_behind(main)
    l, t, r, b = win32gui.GetWindowRect(main)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(main)
    mfc = win32ui.CreateDCFromHandle(hdc)
    mem = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc, w, h)
        mem.SelectObject(bmp)
        if not ctypes.windll.user32.PrintWindow(main, mem.GetSafeHdc(), PW_RENDERFULLCONTENT):
            raise AcadError(tr("Could not capture the AutoCAD window (PrintWindow).",
                               "Falha ao capturar a janela do AutoCAD (PrintWindow)."))
        info = bmp.GetInfo()
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(main, hdc)

    meta: dict[str, Any] = {"window_size": [w, h]}
    cropped = False
    if crop_to_view:
        view = active_view_hwnd(main)
        if view:
            vl, vt, vr, vb = win32gui.GetWindowRect(view)
            box = (max(vl - l, 0), max(vt - t, 0), min(vr - l, w), min(vb - t, h))
            if box[2] - box[0] > 50 and box[3] - box[1] > 50:
                img = img.crop(box)
                cropped = True
                meta["cropped_to_drawing_area"] = True
    if grid is not None and cropped:
        meta["coordinates"] = draw_grid(img, grid["center"], grid["view_height"])
    scale = 1.0
    if img.width > max_width:
        scale = max_width / img.width
        img = img.resize((max_width, int(img.height * scale)), Image.LANCZOS)
    if "coordinates" in meta:
        c = meta["coordinates"]
        c["units_per_pixel"] = c["units_per_pixel"] / scale
    meta["image_size"] = [img.width, img.height]
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), meta


def _nice_step(span: float, target_lines: int = 8) -> float:
    raw = span / max(target_lines, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def draw_grid(img: Image.Image, center: list[float], view_height: float) -> dict[str, Any]:
    """Draw a labelled coordinate grid on the image (plan view in WCS assumed).

    Returns the pixel <-> drawing mapping so the model can read coordinates off the picture.
    """
    W, H = img.size
    upp = view_height / H  # drawing units per pixel
    x0 = center[0] - W / 2 * upp
    y_top = center[1] + H / 2 * upp
    step = _nice_step(max(W, H) * upp)
    d = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default()
    color, label = (0, 200, 255, 90), (0, 220, 255, 255)

    def fmt(v: float) -> str:
        return f"{v:.0f}" if step >= 1 else f"{v:.3g}"

    gx = math.ceil(x0 / step) * step
    while gx <= x0 + W * upp:
        px = (gx - x0) / upp
        d.line([(px, 0), (px, H)], fill=color, width=1)
        d.text((px + 2, H - 12), fmt(gx), fill=label, font=font)
        gx += step
    y_bottom = y_top - H * upp
    gy = math.ceil(y_bottom / step) * step
    while gy <= y_top:
        py = (y_top - gy) / upp
        d.line([(0, py), (W, py)], fill=color, width=1)
        d.text((2, py + 2), fmt(gy), fill=label, font=font)
        gy += step
    return {
        "grid_step": step,
        "units_per_pixel": upp,
        "top_left": [x0, y_top],
        "how_to_read": tr("x = top_left.x + px * units_per_pixel; y = top_left.y - py * units_per_pixel",
                          "x = top_left.x + px * units_per_pixel; y = top_left.y - py * units_per_pixel"),
    }
