"""Zoom and image of the drawing area, so the model can check its own work."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image
from pydantic import Field

from ..acad.capture import capture_view_png
from ..acad.errors import AcadError
from ..acad.util import P, active_doc, bbox, by_handle, pt_out
from ..i18n import t
from .common import VIEW, Ctx, Pt

ZoomMode = Literal["current", "extents", "window", "entities"]
ZOOM_DESC = t("'current' = as is; 'extents' = everything; 'window' = a region; 'entities' = frame handles.",
              "'current' = como está; 'extents' = tudo; 'window' = região; 'entities' = enquadrar handles.")


def do_zoom(app: Any, doc: Any, mode: str, window: list | None, handles: list[str] | None, margin: float) -> dict:
    if mode == "current":
        return {"zoom": "current"}
    if mode == "extents":
        app.ZoomExtents()
        if margin > 0:
            app.ZoomScaled(1.0 / (1.0 + 2 * margin), 1)  # acZoomScaledRelative
        return {"zoom": "extents"}
    if mode == "window":
        if not window or len(window) != 2:
            raise AcadError(t("zoom='window' needs window=[[x1,y1],[x2,y2]].",
                              "zoom='window' precisa de window=[[x1,y1],[x2,y2]]."))
        (x1, y1), (x2, y2) = window[0][:2], window[1][:2]
    elif mode == "entities":
        if not handles:
            raise AcadError(t("zoom='entities' needs handles.", "zoom='entities' precisa de handles."))
        boxes = [b for b in (bbox(by_handle(doc, h)) for h in handles) if b]
        if not boxes:
            raise AcadError(t("Could not compute the extents of those entities.",
                              "Não consegui calcular a extensão dessas entidades."))
        x1, y1 = min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)
        x2, y2 = max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)
    else:
        raise AcadError(t("Invalid zoom.", "zoom inválido."))
    w, h = abs(x2 - x1) or 1.0, abs(y2 - y1) or 1.0
    mx, my = w * margin, h * margin
    lo = [min(x1, x2) - mx, min(y1, y2) - my]
    hi = [max(x1, x2) + mx, max(y1, y2) + my]
    app.ZoomWindow(P(lo), P(hi))
    return {"zoom": mode, "window": [pt_out(lo), pt_out(hi)]}


def register(ctx: Ctx) -> None:
    @ctx.tool(VIEW, title=t("Picture of the AutoCAD screen", "Imagem da tela do AutoCAD"), structured=False, pt=(
        "Tira uma foto da área de desenho do AutoCAD (como o usuário vê). Use depois de desenhar ou editar para "
        "CONFERIR o resultado. grid=true desenha uma grade com coordenadas na foto e devolve como converter pixel "
        "em coordenada — útil para comparar com uma imagem de referência. Muda o zoom, salvo zoom='current'."))
    def get_view_image(
        zoom: Annotated[ZoomMode, Field(description=ZOOM_DESC)] = "extents",
        window: Annotated[list[Pt] | None, Field(description=t("For zoom='window': [[x1,y1],[x2,y2]].",
                                                               "Para zoom='window': [[x1,y1],[x2,y2]]."))] = None,
        handles: Annotated[list[str] | None, Field(description=t("For zoom='entities'.", "Para zoom='entities'."))] = None,
        margin: Annotated[float, Field(ge=0, le=1, description=t("Margin around (fraction).", "Folga em volta (fração)."))] = 0.05,
        grid: Annotated[bool, Field(description=t(
            "Overlay a labelled coordinate grid (plan view) and return the pixel-to-coordinate mapping.",
            "Desenhar grade com coordenadas (vista em planta) e devolver a conversão pixel-coordenada."))] = False,
        max_width: Annotated[int, Field(ge=400, le=2400, description=t("Maximum image width in pixels.",
                                                                       "Largura máxima da imagem em pixels."))] = 1400,
    ) -> list[Any]:
        """Takes a picture of the AutoCAD drawing area (as the user sees it). Use it after drawing or editing to
        CHECK the result. grid=true overlays a coordinate grid and returns how to turn pixels into coordinates —
        useful to compare against a reference image. Changes the zoom unless zoom='current'."""

        def f(app: Any) -> tuple[bytes, dict]:
            doc = active_doc(app)
            info = do_zoom(app, doc, zoom, window, handles, margin)
            if zoom != "current":
                time.sleep(0.5)
            g = None
            if grid:
                g = {"center": list(doc.GetVariable("VIEWCTR"))[:2], "view_height": float(doc.GetVariable("VIEWSIZE"))}
            png, meta = capture_view_png(app, max_width=max_width, grid=g)
            return png, {**info, **meta, "document": doc.Name}

        png, meta = ctx.run(f)
        return [Image(data=png, format="png"), meta]

    @ctx.tool(VIEW, title="Zoom", pt="Muda o zoom da tela do AutoCAD (não altera o desenho). Útil para mostrar algo ao usuário.")
    def zoom_view(
        zoom: Annotated[Literal["extents", "window", "entities"], Field(description=ZOOM_DESC)] = "extents",
        window: Annotated[list[Pt] | None, Field(description=t("For zoom='window': [[x1,y1],[x2,y2]].",
                                                               "Para zoom='window': [[x1,y1],[x2,y2]]."))] = None,
        handles: Annotated[list[str] | None, Field(description=t("For zoom='entities'.", "Para zoom='entities'."))] = None,
        margin: Annotated[float, Field(ge=0, le=1)] = 0.05,
    ) -> dict[str, Any]:
        """Changes the AutoCAD screen zoom (does not modify the drawing). Useful to show something to the user."""
        return ctx.doc(lambda app, doc: do_zoom(app, doc, zoom, window, handles, margin))
