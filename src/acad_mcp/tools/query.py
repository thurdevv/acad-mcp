"""Read tools: drawing info, layers, styles, blocks, entities.

Large drawings are read through in-process LISP (acad/fastpath.py): measured 50,000 entities in
~15 s (full) / ~2 s (light) versus ~22 min through COM. Small drawings, or a minimized window,
use COM. Every response says which engine ran.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Annotated, Any, Literal

import pywintypes
from pydantic import Field

from ..acad import fastpath as F
from ..acad.errors import AcadError
from ..acad.util import (
    _g,
    bbox,
    by_handle,
    color_out,
    entity_info,
    filter_block_name,
    inside_box,
    pt_out,
    rnd,
    select,
    short_type,
)
from ..acad.winutil import is_minimized
from ..i18n import t
from .common import RO, Ctx, Pt
from .documents import units_of

Space = Annotated[
    Literal["model", "paper", "all"],
    Field(description=t("'model' = model space; 'paper' = layouts (sheets); 'all' = both.",
                        "'model' = espaço modelo; 'paper' = layouts (folhas); 'all' = ambos.")),
]


# ------------------------------------------------------------------ engine choice


def use_fast(ctx: Ctx, app: Any, doc: Any, space: str, layout: str | None = None) -> bool:
    if is_minimized(int(app.HWND)) and not ctx.cfg.auto_restore:
        return False
    if (space or "model").startswith("model") and not layout:
        return doc.ModelSpace.Count >= ctx.cfg.fast_threshold
    return True


def _light_com(e: Any) -> dict[str, Any]:
    d = {"handle": _g(e, "Handle"), "type": short_type(_g(e, "ObjectName", "?")), "layer": _g(e, "Layer"),
         "color": color_out(e)}
    if d["type"] == "BlockReference":
        d["name"] = _g(e, "EffectiveName") or _g(e, "Name")
    return d


def fetch(ctx: Ctx, app: Any, doc: Any, *, mode: str = "full", types=None, layers=None, space: str = "model",
          block_name=None, text_contains=None, color=None, within=None, within_mode: str = "crossing",
          offset: int = 0, limit: int = 0, layout: str | None = None) -> dict[str, Any]:
    """Entities as dicts (same keys as entity_info). Returns total, page, engine and seconds."""
    t0 = time.monotonic()
    if block_name and not types:
        types = ["INSERT"]
    py_filter = bool(block_name or within)
    fallback_note = None
    if use_fast(ctx, app, doc, space, layout):
        try:
            flt = F.lisp_filter(types, layers, space, text_contains, color, layout)
            if py_filter:
                total, recs = F.query(app, doc, flt, mode)
                if block_name:
                    import fnmatch

                    pats = [p.strip().upper() for p in block_name.split(",") if p.strip()]
                    recs = [r for r in recs if any(fnmatch.fnmatchcase((r.get("name") or "").upper(), p) for p in pats)]
                if within:
                    recs = [r for r in recs if _in_box(F.approx_bbox(r), within, within_mode)]
                total = len(recs)
                page = recs[offset: offset + limit] if limit else recs[offset:]
            else:
                total, page = F.query(app, doc, flt, mode, offset, limit)
            return {"total": total, "entities": page, "engine": "lisp", "seconds": round(time.monotonic() - t0, 2)}
        except AcadError as exc:
            fallback_note = str(exc)
    ents = select(doc, types=types, layers=layers, space=space, text_contains=text_contains, color=color,
                  layout=layout)
    if block_name:
        ents = filter_block_name(ents, block_name)
    if within:
        ents = [e for e in ents if inside_box(e, within, within_mode)]
    page_ents = ents[offset: offset + limit] if limit else ents[offset:]
    conv = entity_info if mode == "full" else _light_com
    out = {"total": len(ents), "entities": [conv(e) for e in page_ents], "engine": "com",
           "seconds": round(time.monotonic() - t0, 2)}
    if fallback_note:
        out["note"] = t("Fast read failed, used COM: {e}", "Leitura rápida falhou, usei COM: {e}", e=fallback_note)
    return out


def _in_box(b: list | None, box: list, mode: str) -> bool:
    if b is None:
        return False
    (x1, y1), (x2, y2) = box[0][:2], box[1][:2]
    lo, hi = (min(x1, x2), min(y1, y2)), (max(x1, x2), max(y1, y2))
    if mode == "inside":
        return lo[0] <= b[0][0] and lo[1] <= b[0][1] and b[1][0] <= hi[0] and b[1][1] <= hi[1]
    return not (b[1][0] < lo[0] or b[0][0] > hi[0] or b[1][1] < lo[1] or b[0][1] > hi[1])


# Measured on AutoCAD 2027: the 340 (text style) that tblsearch / tblnext return is NOT the style's own, it
# follows the current DIMTXSTY. The record read with entget (tblobjname ...) is right, but leaves some codes
# out, so each value comes from entget when it is there and from tblnext otherwise.
_DIM_LISP = (
    '(progn (defun #g (c) (cdr (cond ((assoc c #e)) ((assoc c #s))))) '
    '(setq #o "" #s (tblnext "DIMSTYLE" T)) (while #s '
    '(setq #e (entget (tblobjname "DIMSTYLE" (cdr (assoc 2 #s)))) #o (strcat #o (cdr (assoc 2 #s))'
    + "".join(f' "|" (rtos (#g {c}) 2 8)' for c in (140, 41, 42, 44, 147, 40, 144))
    + ' "|" (itoa (#g 271)) "|" (if (assoc 340 #e) (cdr (assoc 2 (entget (cdr (assoc 340 #e))))) "")'
    ' ";") #s (tblnext "DIMSTYLE"))) #o)'
)
_DIM_KEYS = ("text_height", "arrow_size", "extension_offset", "extension_beyond", "text_gap", "overall_scale",
             "linear_factor")


def read_dim_styles(app: Any, doc: Any, timeout: float) -> dict[str, dict[str, Any]]:
    """Values stored in every dimension style, read in-process (the COM object exposes only the name)."""
    from ..acad import commands as C

    r = C.run_lisp(app, doc, _DIM_LISP, timeout)
    if r.get("error"):
        raise AcadError(str(r["error"]))
    out: dict[str, dict[str, Any]] = {}
    for rec in str(r.get("result") or "").strip('"').split(";"):
        f = rec.split("|")
        if len(f) < 10 or not f[0]:
            continue
        d: dict[str, Any] = {"name": f[0]}
        for key, raw in zip(_DIM_KEYS, f[1:8]):
            try:
                d[key] = rnd(float(raw))
            except ValueError:
                pass
        try:
            d["decimals"] = int(f[8])
        except ValueError:
            pass
        if f[9]:
            d["text_style"] = f[9]
        out[f[0].upper()] = d
    return out


# ------------------------------------------------------------------ tools


def register(ctx: Ctx) -> None:
    @ctx.tool(RO, title=t("Drawing info", "Informações do desenho"), pt=(
        "Resumo do desenho ativo: unidades, extensão, layer/estilos atuais, escalas, layouts e contagem de "
        "entidades por tipo no espaço modelo."))
    def drawing_info() -> dict[str, Any]:
        """Summary of the active drawing: units, extents, current layer/styles, scales, layouts and entity
        count by type in model space."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            gv = doc.GetVariable
            r = fetch(ctx, app, doc, mode="light", space="model")
            counts = Counter(e["type"] for e in r["entities"])
            return {
                "name": doc.Name,
                "path": doc.FullName or None,
                "units": units_of(doc),
                "extents": {"min": pt_out(gv("EXTMIN")), "max": pt_out(gv("EXTMAX"))},
                "limits": {"min": pt_out(gv("LIMMIN")), "max": pt_out(gv("LIMMAX"))},
                "current": {
                    "layer": doc.ActiveLayer.Name,
                    "text_style": doc.ActiveTextStyle.Name,
                    "dim_style": doc.ActiveDimStyle.Name,
                    "linetype": doc.ActiveLinetype.Name,
                },
                "ltscale": rnd(gv("LTSCALE")),
                "dimscale": rnd(gv("DIMSCALE")),
                "layouts": [doc.Layouts.Item(i).Name for i in range(doc.Layouts.Count)],
                "modelspace_entities_by_type": dict(counts.most_common()),
                "modelspace_total": r["total"],
                "engine": r["engine"],
            }

        return ctx.doc(f)

    @ctx.tool(RO, title=t("List layers", "Listar layers"), pt=(
        "Lista os layers com cor, tipo de linha, espessura e estado (ligado, congelado, travado, plota)."))
    def list_layers(
        with_counts: Annotated[bool, Field(description=t("Count entities per layer (model and layouts).",
                                                         "Contar entidades por layer (modelo e folhas)."))] = True,
    ) -> dict[str, Any]:
        """Lists layers with color, linetype, lineweight and state (on, frozen, locked, plottable)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            counts = None
            if with_counts:
                counts = Counter(e["layer"] for e in fetch(ctx, app, doc, mode="light", space="all")["entities"])
            cur = doc.ActiveLayer.Name
            out = []
            for i in range(doc.Layers.Count):
                ly = doc.Layers.Item(i)
                d = {
                    "name": ly.Name, "color": color_out(ly), "linetype": ly.Linetype, "lineweight": ly.Lineweight,
                    "on": bool(ly.LayerOn), "frozen": bool(ly.Freeze), "locked": bool(ly.Lock),
                    "plottable": bool(ly.Plottable), "description": _g(ly, "Description") or "",
                    "current": ly.Name == cur,
                }
                if counts is not None:
                    d["entities"] = counts.get(ly.Name, 0)
                out.append(d)
            return {"layers": out, "count": len(out),
                    "lineweight_note": t("lineweight in hundredths of mm; -1=ByLayer, -2=ByBlock, -3=Default",
                                         "espessura em centésimos de mm; -1=ByLayer, -2=ByBlock, -3=Padrão")}

        return ctx.doc(f)

    @ctx.tool(RO, title=t("List styles", "Listar estilos"), pt=(
        "Lista estilos de texto (fonte, altura), estilos de cota (com altura de texto, seta, escala, casas "
        "decimais...) e tipos de linha carregados."))
    def list_styles(
        kind: Annotated[Literal["all", "text", "dimension", "linetype"],
                        Field(description=t("Which styles to list.", "Quais estilos listar."))] = "all",
    ) -> dict[str, Any]:
        """Lists text styles (font, height), dimension styles (with text height, arrow size, scale, decimals...)
        and loaded linetypes."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if kind in ("all", "text"):
                out["text_styles"] = [
                    {"name": s.Name, "font_file": s.fontFile, "height": rnd(s.Height), "width_factor": rnd(s.Width),
                     "oblique_deg": rnd(s.ObliqueAngle * 57.29577951308232, 3)}
                    for s in (doc.TextStyles.Item(i) for i in range(doc.TextStyles.Count))
                ]
                out["current_text_style"] = doc.ActiveTextStyle.Name
            if kind in ("all", "dimension"):
                names = [doc.DimStyles.Item(i).Name for i in range(doc.DimStyles.Count)]
                out["current_dim_style"] = doc.ActiveDimStyle.Name
                try:
                    if is_minimized(int(app.HWND)) and not ctx.cfg.auto_restore:
                        raise AcadError(t("AutoCAD window minimized", "janela do AutoCAD minimizada"))
                    values = read_dim_styles(app, doc, ctx.cfg.command_timeout)
                    out["dim_styles"] = [values.get(n.upper(), {"name": n}) for n in names]
                except AcadError as exc:
                    out["dim_styles"] = [{"name": n} for n in names]
                    out["dim_styles_note"] = t("Only the names (values not read: {e}).",
                                               "Só os nomes (valores não lidos: {e}).", e=str(exc)[:120])
            if kind in ("all", "linetype"):
                out["linetypes"] = [{"name": lt.Name, "description": lt.Description}
                                    for lt in (doc.Linetypes.Item(i) for i in range(doc.Linetypes.Count))]
            return out

        return ctx.doc(f)

    @ctx.tool(RO, title=t("List blocks", "Listar blocos"), pt=(
        "Lista as definições de bloco: quantas vezes cada uma foi inserida, tags de atributo, se é dinâmico "
        "ou referência externa (xref)."))
    def list_blocks(
        include_anonymous: Annotated[bool, Field(description=t("Include anonymous blocks (*U, *D...).",
                                                               "Incluir blocos anônimos (*U, *D...)."))] = False,
    ) -> dict[str, Any]:
        """Lists block definitions: how many times each is inserted, attribute tags, whether it is dynamic or
        an external reference (xref)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            refs = fetch(ctx, app, doc, mode="light", types=["INSERT"], space="all")["entities"]
            inserts = Counter(e.get("name") for e in refs)
            out = []
            for i in range(doc.Blocks.Count):
                b = doc.Blocks.Item(i)
                if b.IsLayout or (b.Name.startswith("*") and not include_anonymous):
                    continue
                tags = []
                n_ents = b.Count
                if n_ents <= 2000:
                    for k in range(n_ents):
                        it = b.Item(k)
                        if _g(it, "ObjectName") == "AcDbAttributeDefinition":
                            tags.append(it.TagString)
                out.append({
                    "name": b.Name, "insert_count": inserts.get(b.Name, 0), "attribute_tags": tags,
                    "is_xref": bool(_g(b, "IsXRef", False)), "is_dynamic": bool(_g(b, "IsDynamicBlock", False)),
                    "entities_inside": n_ents, "origin": pt_out(b.Origin),
                })
            out.sort(key=lambda d: (-d["insert_count"], d["name"]))
            return {"blocks": out, "count": len(out)}

        return ctx.doc(f)

    @ctx.tool(RO, title=t("Find entities", "Buscar entidades"), pt=(
        "Busca entidades por tipo, layer, bloco, texto, cor e região. Devolve handle, tipo, layer e a geometria "
        "principal (pontos, comprimento, área, texto, atributos). Use os handles nas ferramentas de edição. "
        "Para totais prefira quantities. Rápido mesmo em desenhos grandes."))
    def query_entities(
        types: Annotated[list[str] | None, Field(description=t(
            "Types: line, circle, arc, polyline, text, mtext, anytext, block, dimension, hatch, point, spline, "
            "ellipse, leader, table, or a DXF name (e.g. LWPOLYLINE).",
            "Tipos: line, circle, arc, polyline, text, mtext, anytext, block, dimension, hatch, point, spline, "
            "ellipse, leader, table, ou nome DXF (ex.: LWPOLYLINE)."))] = None,
        layers: Annotated[list[str] | None, Field(description=t("Layers (wildcards allowed: 'WALL*').",
                                                                "Layers (aceita curingas: 'PAR*')."))] = None,
        block_name: Annotated[str | None, Field(description=t(
            "Only blocks with this name (wildcards and commas: 'DOOR*,WINDOW'). Includes dynamic blocks.",
            "Só blocos com este nome (curingas e vírgula: 'PORTA*,JANELA'). Inclui blocos dinâmicos."))] = None,
        text_contains: Annotated[str | None, Field(description=t("Texts containing this.",
                                                                 "Textos que contêm este trecho."))] = None,
        color: Annotated[int | None, Field(description=t("Only entities with this own ACI color (not ByLayer).",
                                                         "Só entidades com esta cor ACI própria (não ByLayer)."))] = None,
        within: Annotated[list[Pt] | None, Field(description=t(
            "Rectangle [[x1,y1],[x2,y2]] to filter by region (texts/blocks use their insertion point).",
            "Retângulo [[x1,y1],[x2,y2]] para filtrar por região (textos/blocos usam o ponto de inserção)."))] = None,
        within_mode: Annotated[Literal["crossing", "inside"], Field(description=t(
            "'crossing' = touches the region; 'inside' = fully inside.",
            "'crossing' = toca a região; 'inside' = totalmente dentro."))] = "crossing",
        space: Space = "model",
        layout: Annotated[str | None, Field(description=t(
            "Only this layout (sheet), by name (wildcards allowed); replaces space. 'Model' = model space.",
            "Só esta folha (layout), pelo nome (aceita curingas); substitui space. 'Model' = espaço modelo."))] = None,
        offset: Annotated[int, Field(ge=0, description=t("Skip the first N results (paging).",
                                                         "Pular os primeiros N resultados (paginação)."))] = 0,
        limit: Annotated[int, Field(ge=1, le=1000, description=t("Maximum results.", "Máximo de resultados."))] = 100,
        include_bbox: Annotated[bool, Field(description=t("Include each entity's exact bounding box.",
                                                          "Incluir a caixa envolvente exata de cada entidade."))] = False,
    ) -> dict[str, Any]:
        """Finds entities by type, layer, block, text, color and region. Returns handle, type, layer and main
        geometry (points, length, area, text, attributes). Use the handles in editing tools. For totals prefer
        quantities. Fast even on large drawings."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            r = fetch(ctx, app, doc, mode="full", types=types, layers=layers, space=space, block_name=block_name,
                      text_contains=text_contains, color=color, within=within, within_mode=within_mode,
                      offset=offset, limit=limit, layout=layout)
            page = r["entities"]
            if include_bbox:
                for e in page:
                    try:
                        e["bbox"] = bbox(by_handle(doc, e["handle"]))
                    except (AcadError, pywintypes.com_error):
                        e["bbox"] = None
            out = {"total": r["total"], "offset": offset, "returned": len(page),
                   "has_more": offset + len(page) < r["total"], "engine": r["engine"], "seconds": r["seconds"],
                   "entities": page}
            if "note" in r:
                out["note"] = r["note"]
            return out

        return ctx.doc(f)

    @ctx.tool(RO, title=t("Entity details", "Detalhes da entidade"), pt=(
        "Todos os detalhes de uma entidade: geometria completa, propriedades, caixa envolvente, atributos "
        "(blocos), células (tabelas)."))
    def get_entity(
        handle: Annotated[str, Field(description=t("Entity handle.", "Handle da entidade."))],
    ) -> dict[str, Any]:
        """All details of one entity: full geometry, properties, bounding box, attributes (blocks), cells (tables)."""
        return ctx.doc(lambda app, doc: entity_info(by_handle(doc, handle), detail=True))

    @ctx.tool(RO, title=t("User's current selection", "Seleção atual do usuário"), pt=(
        "Entidades que o usuário deixou selecionadas (grips azuis) no AutoCAD agora. Útil quando o usuário diz "
        "'isto que eu selecionei'. Se vier vazio, peça para selecionar sem comando ativo."))
    def get_selection(
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        """Entities the user has selected (blue grips) in AutoCAD right now. Useful when the user says
        'this thing I selected'. If empty, ask them to select with no command running."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            try:
                ss = doc.PickfirstSelectionSet
                items = [ss.Item(i) for i in range(ss.Count)]
            except pywintypes.com_error:
                items = []
            return {"count": len(items), "entities": [entity_info(e) for e in items[:limit]]}

        return ctx.doc(f)
