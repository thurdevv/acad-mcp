"""Drawing tools. Each call = one undo step."""

from __future__ import annotations

import math
import os
from typing import Annotated, Any, Literal

import pywintypes
from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import (
    P,
    _g,
    apply_props,
    by_handle,
    entity_info,
    flat2d,
    flat3d,
    many_by_handle,
    objs,
    rad,
    short_type,
    target_space,
    undo_group,
)
from ..i18n import t
from .common import ADD, EDIT, Color, Ctx, Handles, Layer, Pt

SpaceArg = Annotated[Literal["model", "paper"], Field(description=t("'model' (default) or 'paper' (active layout).",
                                                                    "'model' (padrão) ou 'paper' (layout ativo)."))]
LT = Annotated[str | None, Field(description=t("Linetype (e.g. DASHED, CENTER, HIDDEN). Empty = ByLayer.",
                                               "Tipo de linha (ex.: DASHED, CENTER, HIDDEN). Vazio = ByLayer."))]
LW = Annotated[float | str | None, Field(description=t("Lineweight in mm (0.35) or 'bylayer'.",
                                                       "Espessura em mm (0.35) ou 'bylayer'."))]

JUSTIFY = {
    "left": 0, "center": 1, "right": 2, "aligned": 3, "middle": 4, "fit": 5,
    "topleft": 6, "topcenter": 7, "topright": 8, "middleleft": 9, "middlecenter": 10,
    "middleright": 11, "bottomleft": 12, "bottomcenter": 13, "bottomright": 14,
}
MTEXT_ATTACH = {
    "topleft": 1, "topcenter": 2, "topright": 3, "middleleft": 4, "middlecenter": 5,
    "middleright": 6, "bottomleft": 7, "bottomcenter": 8, "bottomright": 9,
}


# ------------------------------------------------------------------ builders


def mk_line(sp: Any, start: list[float], end: list[float]) -> Any:
    return sp.AddLine(P(start), P(end))


def mk_polyline(sp: Any, points: list[list[float]], closed: bool = False,
                bulges: list[float] | None = None, width: float | None = None) -> Any:
    if len(points) < 2:
        raise AcadError(t("A polyline needs at least 2 points.", "Polilinha precisa de pelo menos 2 pontos."))
    if any(len(p) > 2 and p[2] != 0 for p in points):
        pl = sp.Add3DPoly(flat3d(points))
        pl.Closed = closed
        return pl
    pl = sp.AddLightWeightPolyline(flat2d(points))
    pl.Closed = closed
    if bulges:
        for i, b in enumerate(bulges[: len(points)]):
            if b:
                pl.SetBulge(i, float(b))
    if width:
        pl.ConstantWidth = float(width)
    return pl


def mk_rectangle(sp: Any, corner1: list[float], corner2: list[float]) -> Any:
    (x1, y1), (x2, y2) = corner1[:2], corner2[:2]
    return mk_polyline(sp, [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], closed=True)


def mk_circle(sp: Any, center: list[float], radius: float) -> Any:
    if radius <= 0:
        raise AcadError(t("Radius must be > 0.", "Raio deve ser > 0."))
    return sp.AddCircle(P(center), float(radius))


def mk_arc(sp: Any, center: list[float], radius: float, start_angle_deg: float, end_angle_deg: float) -> Any:
    if radius <= 0:
        raise AcadError(t("Radius must be > 0.", "Raio deve ser > 0."))
    return sp.AddArc(P(center), float(radius), rad(start_angle_deg), rad(end_angle_deg))


def _key(s: str) -> str:
    return s.lower().replace("_", "").replace(" ", "").replace("-", "")


def mk_text(doc: Any, sp: Any, text: str, insertion: list[float], height: float | None = None,
            rotation_deg: float = 0.0, justify: str | None = None, style: str | None = None,
            multiline: bool = False, width: float | None = None) -> Any:
    h = float(height) if height else float(doc.GetVariable("TEXTSIZE"))
    if multiline or "\n" in text:
        mt = sp.AddMText(P(insertion), float(width or 0), text.replace("\n", "\\P"))
        mt.Height = h
        if rotation_deg:
            mt.Rotation = rad(rotation_deg)
        if justify:
            k = _key(justify)
            if k not in MTEXT_ATTACH:
                raise AcadError(t("justify for multiline text: {o}", "justify para texto multilinha: {o}",
                                  o=list(MTEXT_ATTACH)))
            mt.AttachmentPoint = MTEXT_ATTACH[k]
            mt.InsertionPoint = P(insertion)
        if style:
            mt.StyleName = style
        return mt
    tx = sp.AddText(text, P(insertion), h)
    if rotation_deg:
        tx.Rotation = rad(rotation_deg)
    if style:
        tx.StyleName = style
    if justify:
        k = _key(justify)
        if k not in JUSTIFY:
            raise AcadError(t("Invalid justify. Use: {o}", "justify inválido. Use: {o}", o=list(JUSTIFY)))
        if JUSTIFY[k] != 0:
            tx.Alignment = JUSTIFY[k]
            tx.TextAlignmentPoint = P(insertion)
    return tx


def mk_dimension(doc: Any, sp: Any, kind: str, p1: list[float] | None = None, p2: list[float] | None = None,
                 text_position: list[float] | None = None, angle_deg: float = 0.0,
                 center: list[float] | None = None, handle: str | None = None,
                 leader_length: float = 0.0, vertex: list[float] | None = None) -> Any:
    kind = kind.lower()
    if kind in ("radius", "radial", "diameter", "diametric") and handle:
        e = by_handle(doc, handle)
        if short_type(e.ObjectName) not in ("Circle", "Arc"):
            raise AcadError(t("Radius/diameter dimensions need a circle or an arc.",
                              "Cota de raio/diâmetro precisa de um círculo ou arco."))
        c = list(e.Center)
        r = float(e.Radius)
        ang = math.radians(45)
        if short_type(e.ObjectName) == "Arc":
            ang = e.StartAngle + (e.EndAngle - e.StartAngle) % (2 * math.pi) / 2
        center = c
        p1 = [c[0] + r * math.cos(ang), c[1] + r * math.sin(ang)]
        p2 = [c[0] - r * math.cos(ang), c[1] - r * math.sin(ang)]
    if kind == "aligned":
        _need(p1, p2, text_position)
        return sp.AddDimAligned(P(p1), P(p2), P(text_position))
    if kind in ("linear", "rotated", "horizontal", "vertical"):
        _need(p1, p2, text_position)
        if kind == "horizontal":
            angle_deg = 0.0
        elif kind == "vertical":
            angle_deg = 90.0
        return sp.AddDimRotated(P(p1), P(p2), P(text_position), rad(angle_deg))
    if kind in ("radius", "radial"):
        if center is None or p1 is None:
            raise AcadError(t("Radius dimension: give the circle/arc handle, or center + p1 (point on the curve).",
                              "Cota de raio: informe handle do círculo/arco, ou center + p1 (ponto na curva)."))
        return sp.AddDimRadial(P(center), P(p1), float(leader_length))
    if kind in ("diameter", "diametric"):
        if p1 is None or p2 is None:
            raise AcadError(t("Diameter dimension: give the circle handle, or opposite points p1 and p2.",
                              "Cota de diâmetro: informe handle do círculo, ou p1 e p2 opostos na curva."))
        return sp.AddDimDiametric(P(p1), P(p2), float(leader_length))
    if kind == "angular":
        if vertex is None or p1 is None or p2 is None or text_position is None:
            raise AcadError(t("Angular dimension: give vertex, p1, p2 and text_position.",
                              "Cota angular: informe vertex, p1, p2 e text_position."))
        return sp.AddDimAngular(P(vertex), P(p1), P(p2), P(text_position))
    raise AcadError(t("Dimension kind: aligned, linear (with angle_deg), horizontal, vertical, radius, diameter, angular.",
                      "kind de cota: aligned, linear (com angle_deg), horizontal, vertical, radius, diameter, angular."))


def size_dimension(dim: Any, text_height: float | None, arrow_size: float | None) -> None:
    """Per-entity sizes. Dimensions made through COM do not pick up DIMTXT/DIMASZ command-line overrides
    (seen while recording the demo), so sizes are set on the entity."""
    if text_height:
        dim.TextHeight = float(text_height)
        dim.TextGap = float(text_height) / 4
        dim.ExtensionLineExtend = float(text_height) / 2
        dim.ExtensionLineOffset = float(text_height) / 4
    if arrow_size:
        dim.ArrowheadSize = float(arrow_size)


def _need(*pts: Any) -> None:
    if any(p is None for p in pts):
        raise AcadError(t("Missing points: give p1, p2 and text_position.",
                          "Faltam pontos: informe p1, p2 e text_position."))


def mk_point(sp: Any, position: list[float]) -> Any:
    return sp.AddPoint(P(position))


BATCH_KINDS = {
    "line": "start, end",
    "polyline": "points, closed?, bulges?, width?",
    "rectangle": "corner1, corner2",
    "circle": "center, radius",
    "arc": "center, radius, start_angle_deg, end_angle_deg",
    "text": "text, insertion, height?, rotation_deg?, justify?, style?",
    "mtext": "text, insertion, height?, width?, justify?",
    "point": "position",
    "dimension": "kind, p1, p2, text_position, angle_deg?, text_height?, arrow_size? (or handle for radius/diameter)",
}


def build_item(doc: Any, sp: Any, item: dict[str, Any]) -> Any:
    it = dict(item)
    kind = str(it.pop("type", "")).lower()
    for k in ("layer", "color", "linetype", "lineweight"):
        it.pop(k, None)
    try:
        if kind == "line":
            return mk_line(sp, it["start"], it["end"])
        if kind in ("polyline", "pline"):
            return mk_polyline(sp, it["points"], bool(it.get("closed", False)), it.get("bulges"), it.get("width"))
        if kind in ("rectangle", "rect"):
            return mk_rectangle(sp, it["corner1"], it["corner2"])
        if kind == "circle":
            return mk_circle(sp, it["center"], it["radius"])
        if kind == "arc":
            return mk_arc(sp, it["center"], it["radius"], it["start_angle_deg"], it["end_angle_deg"])
        if kind == "text":
            return mk_text(doc, sp, it["text"], it["insertion"], it.get("height"), it.get("rotation_deg", 0.0),
                           it.get("justify"), it.get("style"))
        if kind == "mtext":
            return mk_text(doc, sp, it["text"], it["insertion"], it.get("height"), it.get("rotation_deg", 0.0),
                           it.get("justify"), it.get("style"), multiline=True, width=it.get("width"))
        if kind == "point":
            return mk_point(sp, it["position"])
        if kind in ("dimension", "dim"):
            dim = mk_dimension(doc, sp, it.get("kind", "aligned"), it.get("p1"), it.get("p2"),
                               it.get("text_position"), it.get("angle_deg", 0.0), it.get("center"),
                               it.get("handle"), it.get("leader_length", 0.0), it.get("vertex"))
            size_dimension(dim, it.get("text_height"), it.get("arrow_size"))
            return dim
    except KeyError as exc:
        raise AcadError(t("Item '{k}' is missing field {f}. Fields of {k}: {fs}",
                          "Item '{k}' sem o campo {f}. Campos de {k}: {fs}",
                          k=kind, f=exc, fs=BATCH_KINDS.get(kind))) from None
    raise AcadError(t("Type '{k}' not supported in a batch. Types: {o}",
                      "Tipo '{k}' não suportado no lote. Tipos: {o}", k=kind, o=BATCH_KINDS))


# ------------------------------------------------------------------ registration


def register(ctx: Ctx) -> None:
    def draw(fn_build, layer, color, space="model", linetype=None, lineweight=None):
        def f(app: Any, doc: Any) -> dict[str, Any]:
            sp = target_space(doc, space)
            with undo_group(doc):
                ent = fn_build(doc, sp)
                notes = apply_props(app, doc, ent, layer, color, linetype, lineweight)
            out = entity_info(ent)
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Draw line", "Desenhar linha"), pt="Desenha uma linha reta de start a end. Devolve o handle.")
    def draw_line(start: Pt, end: Pt, layer: Layer = None, color: Color = None,
                  linetype: LT = None, lineweight: LW = None, space: SpaceArg = "model") -> dict[str, Any]:
        """Draws a straight line from start to end. Returns its handle."""
        return draw(lambda d, sp: mk_line(sp, start, end), layer, color, space, linetype, lineweight)

    @ctx.tool(ADD, title=t("Draw polyline", "Desenhar polilinha"), pt=(
        "Desenha uma polilinha (aberta ou fechada) por uma lista de pontos. Fechada tem área calculada."))
    def draw_polyline(
        points: Annotated[list[Pt], Field(min_length=2, description=t("Vertices in order.", "Vértices em ordem."))],
        closed: bool = False,
        bulges: Annotated[list[float] | None, Field(description=t(
            "Bulge per vertex (0 = straight; 1 = half circle). Optional.",
            "Curvatura por vértice (0 = reto; 1 = meio círculo). Opcional."))] = None,
        width: Annotated[float | None, Field(description=t("Constant polyline width.",
                                                           "Largura constante da polilinha."))] = None,
        layer: Layer = None, color: Color = None, linetype: LT = None, lineweight: LW = None,
        space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Draws a polyline (open or closed) through a list of points. Closed ones report their area."""
        return draw(lambda d, sp: mk_polyline(sp, points, closed, bulges, width), layer, color, space, linetype, lineweight)

    @ctx.tool(ADD, title=t("Draw rectangle", "Desenhar retângulo"),
              pt="Desenha um retângulo (polilinha fechada) pelos cantos opostos.")
    def draw_rectangle(corner1: Pt, corner2: Pt, layer: Layer = None, color: Color = None,
                       linetype: LT = None, lineweight: LW = None, space: SpaceArg = "model") -> dict[str, Any]:
        """Draws a rectangle (closed polyline) from opposite corners."""
        return draw(lambda d, sp: mk_rectangle(sp, corner1, corner2), layer, color, space, linetype, lineweight)

    @ctx.tool(ADD, title=t("Draw circle", "Desenhar círculo"), pt="Desenha um círculo por centro e raio.")
    def draw_circle(center: Pt, radius: Annotated[float, Field(gt=0)], layer: Layer = None, color: Color = None,
                    linetype: LT = None, lineweight: LW = None, space: SpaceArg = "model") -> dict[str, Any]:
        """Draws a circle from center and radius."""
        return draw(lambda d, sp: mk_circle(sp, center, radius), layer, color, space, linetype, lineweight)

    @ctx.tool(ADD, title=t("Draw arc", "Desenhar arco"), pt="Desenha um arco anti-horário do ângulo inicial ao final.")
    def draw_arc(center: Pt, radius: Annotated[float, Field(gt=0)],
                 start_angle_deg: Annotated[float, Field(description=t(
                     "Start angle (degrees, 0 = east, counter-clockwise).",
                     "Ângulo inicial (graus, 0 = leste, anti-horário)."))],
                 end_angle_deg: Annotated[float, Field(description=t("End angle (degrees).", "Ângulo final (graus)."))],
                 layer: Layer = None, color: Color = None, linetype: LT = None, lineweight: LW = None,
                 space: SpaceArg = "model") -> dict[str, Any]:
        """Draws a counter-clockwise arc from the start angle to the end angle."""
        return draw(lambda d, sp: mk_arc(sp, center, radius, start_angle_deg, end_angle_deg), layer, color, space,
                    linetype, lineweight)

    @ctx.tool(ADD, title=t("Write text", "Escrever texto"), pt="Escreve texto simples (TEXT) ou multilinha (MTEXT).")
    def draw_text(
        text: Annotated[str, Field(description=t("Content. A line break (\\n) makes multiline text.",
                                                 "Conteúdo. Quebra de linha (\\n) cria texto multilinha."))],
        insertion: Pt,
        height: Annotated[float | None, Field(gt=0, description=t("Text height. Empty = current TEXTSIZE.",
                                                                  "Altura do texto. Vazio = TEXTSIZE atual."))] = None,
        rotation_deg: float = 0.0,
        justify: Annotated[str | None, Field(description="left, center, right, middle, topleft, middlecenter, bottomright...")] = None,
        style: Annotated[str | None, Field(description=t("Existing text style.", "Estilo de texto existente."))] = None,
        multiline: Annotated[bool, Field(description=t("Force MText (multiline text).",
                                                       "Forçar MText (texto multilinha)."))] = False,
        width: Annotated[float | None, Field(description=t("MText box width (word wrap).",
                                                           "Largura da caixa do MText (quebra automática)."))] = None,
        layer: Layer = None, color: Color = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Writes single-line (TEXT) or multiline (MTEXT) text."""
        return draw(lambda d, sp: mk_text(d, sp, text, insertion, height, rotation_deg, justify, style, multiline, width),
                    layer, color, space)

    @ctx.tool(ADD, title=t("Dimension", "Cotar"), pt=(
        "Cria cota: alinhada, linear (horizontal/vertical/ângulo), raio, diâmetro ou angular. Devolve a medida lida "
        "pelo AutoCAD."))
    def draw_dimension(
        kind: Annotated[Literal["aligned", "linear", "horizontal", "vertical", "radius", "diameter", "angular"],
                        Field(description=t("Dimension type.", "Tipo de cota."))],
        p1: Annotated[Pt | None, Field(description=t("1st measured point (or point on the curve, for radius).",
                                                     "1º ponto medido (ou ponto na curva, p/ raio)."))] = None,
        p2: Annotated[Pt | None, Field(description=t("2nd measured point.", "2º ponto medido."))] = None,
        text_position: Annotated[Pt | None, Field(description=t("Where the dimension line/text goes.",
                                                                "Onde fica a linha de cota/texto."))] = None,
        angle_deg: Annotated[float, Field(description=t("Only for 'linear': measuring direction (0 = horizontal).",
                                                        "Só p/ 'linear': direção da medida (0 = horizontal)."))] = 0.0,
        handle: Annotated[str | None, Field(description=t("For radius/diameter: circle or arc handle.",
                                                          "Para raio/diâmetro: handle do círculo ou arco."))] = None,
        center: Pt | None = None,
        vertex: Annotated[Pt | None, Field(description=t("For angular: the angle's vertex.",
                                                         "Para angular: vértice do ângulo."))] = None,
        leader_length: float = 0.0,
        style: Annotated[str | None, Field(description=t("Dimension style. Empty = current.",
                                                         "Estilo de cota. Vazio = atual."))] = None,
        text_override: Annotated[str | None, Field(description=t(
            "Replaces the measured text. Use '<>' to include the measurement.",
            "Substitui o texto medido. Use '<>' para incluir a medida."))] = None,
        text_height: Annotated[float | None, Field(gt=0, description=t(
            "Dimension text height in drawing units (e.g. 200 on a mm plan). Empty = dimension style.",
            "Altura do texto da cota em unidades do desenho (ex.: 200 numa planta em mm). Vazio = estilo de cota."))] = None,
        arrow_size: Annotated[float | None, Field(gt=0, description=t(
            "Arrow size in drawing units. Empty = dimension style.",
            "Tamanho da seta em unidades do desenho. Vazio = estilo de cota."))] = None,
        layer: Layer = None, color: Color = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Creates a dimension: aligned, linear (horizontal/vertical/angle), radius, diameter or angular. Returns the
        measurement read by AutoCAD. If the text looks tiny (e.g. an inch template on a mm drawing), pass
        text_height and arrow_size."""

        def build(d: Any, sp: Any) -> Any:
            dim = mk_dimension(d, sp, kind, p1, p2, text_position, angle_deg, center, handle, leader_length, vertex)
            if style:
                dim.StyleName = style
            if text_override:
                dim.TextOverride = text_override
            size_dimension(dim, text_height, arrow_size)
            return dim

        return draw(build, layer, color, space)

    @ctx.tool(ADD, title=t("Hatch", "Hachurar"), pt="Hachura a área dentro de contornos fechados existentes (associativa).")
    def draw_hatch(
        boundary_handles: Annotated[list[str], Field(min_length=1, description=t(
            "CLOSED boundaries (closed polylines, circles).", "Contornos FECHADOS (polilinhas fechadas, círculos)."))],
        pattern: Annotated[str, Field(description=t(
            "Pattern: SOLID, ANSI31, ANSI37, AR-CONC, AR-SAND, NET, GRAVEL, EARTH...",
            "Padrão: SOLID, ANSI31, ANSI37, AR-CONC, AR-SAND, NET, GRAVEL, EARTH..."))] = "ANSI31",
        scale: Annotated[float, Field(gt=0)] = 1.0,
        angle_deg: float = 0.0,
        layer: Layer = None, color: Color = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Hatches the area inside existing closed boundaries (associative)."""

        def build(d: Any, sp: Any) -> Any:
            loops = many_by_handle(d, boundary_handles)
            h = sp.AddHatch(1, pattern.upper(), True)
            try:
                h.AppendOuterLoop(objs(loops))
                if pattern.upper() != "SOLID":
                    h.PatternScale = float(scale)
                    h.PatternAngle = rad(angle_deg)
                h.Evaluate()
            except pywintypes.com_error as exc:
                h.Delete()
                raise AcadError(t(
                    "Could not hatch: boundaries must be closed and not self-intersecting, and the pattern must exist "
                    "in acad.pat. ({e})",
                    "Não consegui hachurar: os contornos precisam ser fechados e não se cruzar, e o padrão precisa "
                    "existir em acad.pat. ({e})", e=exc.args[1])) from None
            return h

        return draw(build, layer, color, space)

    @ctx.tool(ADD, title=t("Leader", "Chamada (leader)"), pt=(
        "Cria uma chamada (multileader) com seta apontando para o primeiro ponto e o texto no último."))
    def draw_leader(
        points: Annotated[list[Pt], Field(min_length=2, description=t(
            "Arrow tip first, then the points up to the text.", "Ponta da seta primeiro, depois os pontos até o texto."))],
        text: str,
        text_height: Annotated[float | None, Field(gt=0)] = None,
        layer: Layer = None, color: Color = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Creates a multileader with the arrow at the first point and the text at the last."""

        def build(d: Any, sp: Any) -> Any:
            res = sp.AddMLeader(flat3d(points), 0)
            ml = res[0] if isinstance(res, tuple) else res
            ml.TextString = text
            if text_height:
                ml.TextHeight = float(text_height)
            return ml

        return draw(build, layer, color, space)

    @ctx.tool(ADD, title=t("Table", "Tabela"), pt=(
        "Cria uma tabela do AutoCAD (ACAD_TABLE) com os textos informados — ex.: lista de materiais."))
    def draw_table(
        insertion: Annotated[Pt, Field(description=t("Top-left corner.", "Canto superior esquerdo."))],
        rows: Annotated[list[list[str]], Field(min_length=1, description=t(
            "Rows of cells (text). The first may be the header.", "Linhas de células (texto). A 1ª pode ser o cabeçalho."))],
        title: Annotated[str | None, Field(description=t("Title (merged row on top). Optional.",
                                                         "Título (linha mesclada no topo). Opcional."))] = None,
        column_widths: Annotated[list[float] | None, Field(description=t("Width of each column. Empty = 30.",
                                                                         "Largura de cada coluna. Vazio = 30."))] = None,
        row_height: Annotated[float, Field(gt=0)] = 8.0,
        text_height: Annotated[float | None, Field(gt=0)] = None,
        layer: Layer = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Creates an AutoCAD table (ACAD_TABLE) with the given texts — e.g. a bill of materials."""

        def build(d: Any, sp: Any) -> Any:
            ncols = max(len(r) for r in rows)
            nrows = len(rows) + (1 if title else 0)
            widths = column_widths or [30.0] * ncols
            tb = sp.AddTable(P(insertion), nrows, ncols, float(row_height), float(widths[0]))
            off = 0
            if title:
                tb.SetText(0, 0, title)
                off = 1
            else:
                try:
                    tb.UnmergeCells(0, 0, 0, ncols - 1)
                except pywintypes.com_error:
                    pass
            for c in range(ncols):
                tb.SetColumnWidth(c, float(widths[c] if c < len(widths) else widths[-1]))
            for r, row in enumerate(rows):
                for c in range(ncols):
                    tb.SetText(r + off, c, str(row[c]) if c < len(row) else "")
                    if text_height:
                        tb.SetCellTextHeight(r + off, c, float(text_height))
            return tb

        return draw(build, layer, None, space)

    @ctx.tool(ADD, title=t("Insert block", "Inserir bloco"), pt="Insere um bloco (do desenho ou de um arquivo DWG) e preenche atributos.")
    def insert_block(
        name: Annotated[str, Field(description=t("Name of a block in the drawing, or path of a .dwg to insert as a block.",
                                                 "Nome de um bloco do desenho, ou caminho de um .dwg para inserir como bloco."))],
        insertion: Pt,
        scale: Annotated[float, Field(gt=0)] = 1.0,
        rotation_deg: float = 0.0,
        attributes: Annotated[dict[str, str] | None, Field(description=t(
            "Attribute values by TAG, e.g. {'TAG': 'P-101'}.", "Valores de atributo por TAG, ex.: {'TAG': 'P-101'}."))] = None,
        layer: Layer = None, color: Color = None, space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Inserts a block (from the drawing or from a DWG file) and fills its attributes."""

        def build(d: Any, sp: Any) -> Any:
            if name.lower().endswith(".dwg"):
                if not os.path.isfile(name):
                    raise AcadError(t("File not found: {p}", "Arquivo não encontrado: {p}", p=name))
            else:
                try:
                    d.Blocks.Item(name)
                except pywintypes.com_error:
                    raise AcadError(t("Block '{n}' does not exist in this drawing. See list_blocks.",
                                      "Bloco '{n}' não existe neste desenho. Veja list_blocks.", n=name)) from None
            br = sp.InsertBlock(P(insertion), name, float(scale), float(scale), float(scale), rad(rotation_deg))
            if attributes:
                wanted = {k.upper(): v for k, v in attributes.items()}
                found = set()
                if _g(br, "HasAttributes", False):
                    for a in br.GetAttributes():
                        if a.TagString.upper() in wanted:
                            a.TextString = str(wanted[a.TagString.upper()])
                            found.add(a.TagString.upper())
                missing = set(wanted) - found
                if missing:
                    br.Delete()
                    raise AcadError(t("The block has no attributes {m}.", "O bloco não tem os atributos {m}.",
                                      m=sorted(missing)))
            return br

        return draw(build, layer, color, space)

    @ctx.tool(EDIT, title=t("Create block", "Criar bloco"), pt=(
        "Cria uma definição de bloco a partir de entidades existentes (como o comando BLOCK)."))
    def create_block(
        name: Annotated[str, Field(description=t("New block name.", "Nome do novo bloco."))],
        base_point: Annotated[Pt, Field(description=t("Base point (block insertion point).",
                                                      "Ponto base (ponto de inserção do bloco)."))],
        handles: Handles,
        replace_with_insert: Annotated[bool, Field(description=t("Erase the originals and put the block in their place.",
                                                                 "Apagar os originais e pôr o bloco no lugar."))] = True,
    ) -> dict[str, Any]:
        """Creates a block definition from existing entities (like the BLOCK command)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            try:
                doc.Blocks.Item(name)
                raise AcadError(t("A block named '{n}' already exists.", "Já existe um bloco '{n}'.", n=name))
            except pywintypes.com_error:
                pass
            ents = many_by_handle(doc, handles)
            with undo_group(doc):
                blk = doc.Blocks.Add(P([0, 0, 0]), name)
                doc.CopyObjects(objs(ents), blk)
                for i in range(blk.Count):  # base_point becomes the block origin
                    blk.Item(i).Move(P(base_point), P([0, 0, 0]))
                out: dict[str, Any] = {"block": name, "entities_in_block": blk.Count}
                if replace_with_insert:
                    sp = doc.ModelSpace if _g(ents[0], "OwnerID") == doc.ModelSpace.ObjectID else doc.PaperSpace
                    br = sp.InsertBlock(P(base_point), name, 1.0, 1.0, 1.0, 0.0)
                    br.Layer = ents[0].Layer
                    for e in ents:
                        e.Delete()
                    out["insert"] = entity_info(br)
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Draw in batch", "Desenhar em lote"), pt=(
        "Desenha muitos objetos numa única chamada (e um único Ctrl+Z) — o jeito principal de desenhar ou RECRIAR "
        "plantas, detalhes e peças (inclusive a partir de imagem/PDF anexado) direto no AutoCAD, sem gerar arquivo. "
        "Se um item falhar, nada é desenhado e o erro diz qual item."))
    def draw_batch(
        items: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=2000, description=t(
            "List of objects. Each has 'type' and the fields: {k}. All accept their own 'layer' and 'color'.",
            "Lista de objetos. Cada um tem 'type' e os campos: {k}. Todos aceitam 'layer' e 'color' próprios.",
            k="; ".join(f"{k}: {v}" for k, v in BATCH_KINDS.items())))],
        layer: Annotated[str | None, Field(description=t("Default layer for items without 'layer'.",
                                                         "Layer padrão para itens sem 'layer'."))] = None,
        color: Color = None,
        space: SpaceArg = "model",
    ) -> dict[str, Any]:
        """Draws many objects in a single call (and a single undo) — the main way to draw or RECREATE plans, details
        and parts (including from an attached image/PDF) straight in AutoCAD, without generating a file. If one item
        fails, nothing is drawn and the error says which item."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            sp = target_space(doc, space)
            created: list[Any] = []
            notes: set[str] = set()
            with undo_group(doc):
                try:
                    for i, it in enumerate(items):
                        try:
                            ent = build_item(doc, sp, it)
                        except AcadError as exc:
                            raise AcadError(f"Item {i} ({it.get('type')}): {exc}") from None
                        created.append(ent)
                        notes.update(apply_props(app, doc, ent, it.get("layer") or layer,
                                                 it.get("color", color), it.get("linetype"), it.get("lineweight")))
                except Exception:
                    for e in created:
                        try:
                            e.Delete()
                        except pywintypes.com_error:
                            pass
                    raise
            return {"created": len(created), "handles": [e.Handle for e in created],
                    **({"notes": sorted(notes)} if notes else {})}

        return ctx.doc(f)
