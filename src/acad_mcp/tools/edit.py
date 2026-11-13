"""Editing tools. Each call = one undo step."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

import pywintypes
from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import (
    P,
    _g,
    apply_props,
    bbox,
    by_handle,
    entity_info,
    flat2d,
    many_by_handle,
    rad,
    short_type,
    undo_group,
)
from ..i18n import t
from .common import ADD, EDIT, Color, Ctx, Handles, Pt


def _seg_dist(p: list[float], a: list[float], b: list[float]) -> float:
    ax, ay, bx, by, px, py = a[0], a[1], b[0], b[1], p[0], p[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    k = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + k * dx), py - (ay + k * dy))


def _dist_to_curve(o: Any, p: list[float]) -> float:
    """Approximate distance from a point to a curve (to pick the offset side)."""
    typ = short_type(o.ObjectName)
    if typ in ("Circle", "Arc"):
        c = list(o.Center)
        return abs(math.hypot(p[0] - c[0], p[1] - c[1]) - float(o.Radius))
    if typ == "Line":
        return _seg_dist(p, list(o.StartPoint), list(o.EndPoint))
    if typ == "Polyline":
        c = list(o.Coordinates)
        pts = [[c[i], c[i + 1]] for i in range(0, len(c), 2)]
        if bool(o.Closed):
            pts.append(pts[0])
        return min(_seg_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    bb = bbox(o)
    if not bb:
        return math.inf
    return math.hypot((bb[0][0] + bb[1][0]) / 2 - p[0], (bb[0][1] + bb[1][1]) / 2 - p[1])


def _unlocked(doc: Any, ents: list[Any]) -> None:
    locked = sorted({e.Layer for e in ents if doc.Layers.Item(e.Layer).Lock})
    if locked:
        raise AcadError(t("Locked layer(s): {l}. Unlock with manage_layer (locked=false) first.",
                          "Layer(s) travado(s): {l}. Destrave com manage_layer (locked=false) antes.", l=locked))


def register(ctx: Ctx) -> None:
    @ctx.tool(EDIT, title=t("Move/copy/rotate/scale/mirror", "Mover/copiar/girar/escalar/espelhar"), pt=(
        "Move, copia, gira, escala ou espelha entidades. 'copy' e keep_original=true devolvem os handles novos."))
    def transform_entities(
        handles: Handles,
        operation: Annotated[Literal["move", "copy", "rotate", "scale", "mirror"],
                             Field(description=t("Operation.", "Operação."))],
        from_point: Annotated[Pt | None, Field(description=t("move/copy: displacement base point.",
                                                             "move/copy: ponto de origem do deslocamento."))] = None,
        to_point: Annotated[Pt | None, Field(description=t("move/copy: destination point.",
                                                           "move/copy: ponto de destino."))] = None,
        base_point: Annotated[Pt | None, Field(description=t("rotate/scale: base point.",
                                                             "rotate/scale: ponto base."))] = None,
        angle_deg: Annotated[float | None, Field(description=t("rotate: angle in degrees (counter-clockwise).",
                                                               "rotate: ângulo em graus (anti-horário)."))] = None,
        factor: Annotated[float | None, Field(gt=0, description=t("scale: scale factor.", "scale: fator de escala."))] = None,
        mirror_line: Annotated[list[Pt] | None, Field(description=t("mirror: two points of the mirror line.",
                                                                    "mirror: dois pontos da linha de espelho."))] = None,
        keep_original: Annotated[bool, Field(description=t(
            "rotate/scale/mirror: keep the original (works on a copy).",
            "rotate/scale/mirror: manter o original (trabalha numa cópia)."))] = False,
    ) -> dict[str, Any]:
        """Moves, copies, rotates, scales or mirrors entities. 'copy' and keep_original=true return the new handles."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            ents = many_by_handle(doc, handles)
            _unlocked(doc, ents)
            new: list[str] = []
            with undo_group(doc):
                if operation in ("move", "copy"):
                    if from_point is None or to_point is None:
                        raise AcadError(t("move/copy need from_point and to_point.",
                                          "move/copy precisam de from_point e to_point."))
                    for e in ents:
                        tgt = e.Copy() if operation == "copy" else e
                        tgt.Move(P(from_point), P(to_point))
                        if operation == "copy":
                            new.append(tgt.Handle)
                elif operation == "rotate":
                    if base_point is None or angle_deg is None:
                        raise AcadError(t("rotate needs base_point and angle_deg.",
                                          "rotate precisa de base_point e angle_deg."))
                    for e in ents:
                        tgt = e.Copy() if keep_original else e
                        tgt.Rotate(P(base_point), rad(angle_deg))
                        if keep_original:
                            new.append(tgt.Handle)
                elif operation == "scale":
                    if base_point is None or factor is None:
                        raise AcadError(t("scale needs base_point and factor.", "scale precisa de base_point e factor."))
                    for e in ents:
                        tgt = e.Copy() if keep_original else e
                        tgt.ScaleEntity(P(base_point), float(factor))
                        if keep_original:
                            new.append(tgt.Handle)
                elif operation == "mirror":
                    if not mirror_line or len(mirror_line) != 2:
                        raise AcadError(t("mirror needs mirror_line=[[x1,y1],[x2,y2]].",
                                          "mirror precisa de mirror_line=[[x1,y1],[x2,y2]]."))
                    for e in ents:
                        m = e.Mirror(P(mirror_line[0]), P(mirror_line[1]))
                        new.append(m.Handle)
                        if not keep_original:
                            e.Delete()
            out: dict[str, Any] = {"operation": operation, "count": len(ents)}
            if new:
                out["new_handles"] = new
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Array", "Matriz (array)"), pt=(
        "Repete entidades em grade (linhas x colunas) ou em círculo. Devolve os handles criados."))
    def array_entities(
        handles: Handles,
        kind: Annotated[Literal["rectangular", "polar"], Field(description=t("Array type.", "Tipo de matriz."))],
        rows: Annotated[int, Field(ge=1)] = 1,
        columns: Annotated[int, Field(ge=1)] = 1,
        row_spacing: Annotated[float, Field(description=t("rectangular: distance between rows (Y).",
                                                          "rectangular: distância entre linhas (Y)."))] = 0.0,
        column_spacing: Annotated[float, Field(description=t("rectangular: distance between columns (X).",
                                                             "rectangular: distância entre colunas (X)."))] = 0.0,
        count: Annotated[int, Field(ge=2, description=t("polar: total items, including the original.",
                                                        "polar: total de itens, incluindo o original."))] = 4,
        center: Annotated[Pt | None, Field(description=t("polar: center.", "polar: centro."))] = None,
        fill_angle_deg: Annotated[float, Field(description=t("polar: angle to fill.", "polar: ângulo a preencher."))] = 360.0,
    ) -> dict[str, Any]:
        """Repeats entities in a grid (rows x columns) or around a center. Returns the created handles."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            ents = many_by_handle(doc, handles)
            new: list[str] = []
            with undo_group(doc):
                for e in ents:
                    if kind == "rectangular":
                        if rows * columns < 2:
                            raise AcadError(t("Use rows/columns giving at least 2 items.",
                                              "Use rows/columns que somem pelo menos 2 itens."))
                        res = e.ArrayRectangular(rows, columns, 1, float(row_spacing), float(column_spacing), 0.0)
                    else:
                        if center is None:
                            raise AcadError(t("polar needs center.", "polar precisa de center."))
                        res = e.ArrayPolar(count, rad(fill_angle_deg), P(center))
                    new += [o.Handle for o in res]
            return {"new_handles": new, "created": len(new)}

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Offset", "Offset (paralela)"), pt=(
        "Cria uma cópia paralela (offset) de linha, polilinha, arco ou círculo a uma distância."))
    def offset_entity(
        handle: str,
        distance: Annotated[float, Field(gt=0)],
        toward_point: Annotated[Pt | None, Field(description=t(
            "A point on the wanted side. Empty = AutoCAD's default side (may be inside or outside).",
            "Ponto do lado desejado. Vazio = lado padrão do AutoCAD (pode ser o de dentro ou de fora)."))] = None,
    ) -> dict[str, Any]:
        """Creates a parallel copy (offset) of a line, polyline, arc or circle at a distance."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            e = by_handle(doc, handle)
            with undo_group(doc):
                if toward_point is None:
                    res = list(e.Offset(float(distance)))
                else:
                    a = list(e.Offset(float(distance)))
                    b = list(e.Offset(-float(distance)))

                    def dist(group: list[Any]) -> float:
                        return min((_dist_to_curve(o, toward_point) for o in group), default=math.inf)

                    keep, drop = (a, b) if dist(a) <= dist(b) else (b, a)
                    for o in drop:
                        o.Delete()
                    res = keep
            return {"new": [entity_info(o) for o in res]}

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Erase entities", "Apagar entidades"), pt="Apaga entidades pelo handle (desfazível com undo).")
    def erase_entities(handles: Handles) -> dict[str, Any]:
        """Erases entities by handle (undo reverts it)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            ents = many_by_handle(doc, handles)
            _unlocked(doc, ents)
            with undo_group(doc):
                for e in ents:
                    e.Delete()
            return {"erased": len(ents)}

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Change properties", "Mudar propriedades"), pt=(
        "Muda layer, cor, tipo de linha, espessura ou escala de linha de várias entidades de uma vez."))
    def set_entity_properties(
        handles: Handles,
        layer: Annotated[str | None, Field(description=t("Move to this layer (created if missing).",
                                                         "Mover para este layer (criado se não existir)."))] = None,
        color: Color = None,
        linetype: Annotated[str | None, Field(description=t("Linetype or 'ByLayer'.", "Tipo de linha ou 'ByLayer'."))] = None,
        lineweight: Annotated[float | str | None, Field(description=t("mm (0.35) or 'bylayer'.", "mm (0.35) ou 'bylayer'."))] = None,
        linetype_scale: Annotated[float | None, Field(gt=0)] = None,
    ) -> dict[str, Any]:
        """Changes layer, color, linetype, lineweight or linetype scale of many entities at once."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            ents = many_by_handle(doc, handles)
            notes: set[str] = set()
            with undo_group(doc):
                for e in ents:
                    notes.update(apply_props(app, doc, e, layer, color, linetype, lineweight, linetype_scale))
            return {"updated": len(ents), **({"notes": sorted(notes)} if notes else {})}

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Edit text", "Editar texto"), pt=(
        "Edita texto (TEXT, MTEXT), texto de cota, chamada (multileader) ou atributo."))
    def edit_text(
        handle: str,
        text: Annotated[str | None, Field(description=t(
            "New content. On dimensions it replaces the value ('<>' = measurement).",
            "Novo conteúdo. Em cotas, substitui o valor ('<>' = medida)."))] = None,
        height: Annotated[float | None, Field(gt=0)] = None,
        rotation_deg: float | None = None,
        insertion: Pt | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        """Edits text (TEXT, MTEXT), dimension text, multileader text or an attribute."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            e = by_handle(doc, handle)
            typ = short_type(e.ObjectName)
            is_dim = "Dimension" in e.ObjectName
            with undo_group(doc):
                if text is not None:
                    if is_dim:
                        e.TextOverride = text
                    elif typ == "MText":
                        e.TextString = text.replace("\n", "\\P")
                    elif typ in ("Text", "AttributeReference", "AttributeDefinition", "MLeader"):
                        e.TextString = text
                    else:
                        raise AcadError(t("{k} has no editable text.", "Entidade {k} não tem texto editável.", k=typ))
                if height is not None:
                    if is_dim or typ == "MLeader":
                        e.TextHeight = float(height)
                    else:
                        e.Height = float(height)
                if rotation_deg is not None:
                    if is_dim:
                        e.TextRotation = rad(rotation_deg)
                    else:
                        e.Rotation = rad(rotation_deg)
                if insertion is not None:
                    if is_dim:
                        e.TextPosition = P(insertion)
                    elif typ == "Text" and _g(e, "Alignment", 0) != 0:
                        e.TextAlignmentPoint = P(insertion)
                    else:
                        e.InsertionPoint = P(insertion)
                if style is not None:
                    e.StyleName = style
            return entity_info(e)

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Edit block attributes", "Editar atributos de bloco"), pt=(
        "Altera valores de atributos de um bloco inserido (ex.: tag, descrição, carimbo)."))
    def set_block_attributes(
        handle: Annotated[str, Field(description=t("Block reference handle.", "Handle da referência de bloco."))],
        values: Annotated[dict[str, str], Field(description=t(
            "New values by TAG, e.g. {'TAG': 'P-102', 'DESC': 'Pump'}.",
            "Novos valores por TAG, ex.: {'TAG': 'P-102', 'DESC': 'Bomba'}."))],
    ) -> dict[str, Any]:
        """Changes attribute values of an inserted block (tag, description, title block...)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            br = by_handle(doc, handle)
            if short_type(br.ObjectName) != "BlockReference" or not _g(br, "HasAttributes", False):
                raise AcadError(t("That entity is not a block with attributes.",
                                  "Essa entidade não é um bloco com atributos."))
            wanted = {k.upper(): v for k, v in values.items()}
            atts = list(br.GetAttributes())
            tags = {a.TagString.upper() for a in atts}
            missing = set(wanted) - tags
            if missing:
                raise AcadError(t("Unknown tags: {m}. The block's tags: {b}",
                                  "Tags inexistentes: {m}. Tags do bloco: {b}", m=sorted(missing), b=sorted(tags)))
            with undo_group(doc):
                for a in atts:
                    if a.TagString.upper() in wanted:
                        a.TextString = str(wanted[a.TagString.upper()])
            return entity_info(br)

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Change geometry", "Alterar geometria"), pt=(
        "Altera diretamente pontos, raio, ângulos, vértices, inserção, escala ou rotação de uma entidade."))
    def modify_geometry(
        handle: str,
        start: Annotated[Pt | None, Field(description=t("Line: new start point.", "Linha: novo ponto inicial."))] = None,
        end: Annotated[Pt | None, Field(description=t("Line: new end point.", "Linha: novo ponto final."))] = None,
        center: Annotated[Pt | None, Field(description=t("Circle/arc: new center.", "Círculo/arco: novo centro."))] = None,
        radius: Annotated[float | None, Field(gt=0, description=t("Circle/arc: new radius.", "Círculo/arco: novo raio."))] = None,
        start_angle_deg: float | None = None,
        end_angle_deg: float | None = None,
        vertices: Annotated[list[Pt] | None, Field(description=t("Polyline: full new vertex list.",
                                                                 "Polilinha: nova lista completa de vértices."))] = None,
        closed: Annotated[bool | None, Field(description=t("Polyline: close/open.", "Polilinha: fechar/abrir."))] = None,
        insertion: Annotated[Pt | None, Field(description=t("Block/text: new insertion point.",
                                                            "Bloco/texto: novo ponto de inserção."))] = None,
        scale: Annotated[float | None, Field(gt=0, description=t("Block: new uniform scale.", "Bloco: nova escala uniforme."))] = None,
        rotation_deg: Annotated[float | None, Field(description=t("Block/text: new rotation.", "Bloco/texto: nova rotação."))] = None,
    ) -> dict[str, Any]:
        """Directly changes points, radius, angles, vertices, insertion, scale or rotation of one entity."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            e = by_handle(doc, handle)
            typ = short_type(e.ObjectName)
            with undo_group(doc):
                try:
                    if start is not None:
                        e.StartPoint = P(start)
                    if end is not None:
                        e.EndPoint = P(end)
                    if center is not None:
                        e.Center = P(center)
                    if radius is not None:
                        e.Radius = float(radius)
                    if start_angle_deg is not None:
                        e.StartAngle = rad(start_angle_deg)
                    if end_angle_deg is not None:
                        e.EndAngle = rad(end_angle_deg)
                    if vertices is not None:
                        if typ != "Polyline":
                            raise AcadError(t("vertices only applies to polylines (LWPOLYLINE).",
                                              "vertices só vale para polilinha (LWPOLYLINE)."))
                        e.Coordinates = flat2d(vertices)
                    if closed is not None:
                        e.Closed = bool(closed)
                    if insertion is not None:
                        e.InsertionPoint = P(insertion)
                    if scale is not None:
                        e.XScaleFactor = e.YScaleFactor = e.ZScaleFactor = float(scale)
                    if rotation_deg is not None:
                        e.Rotation = rad(rotation_deg)
                except (AttributeError, pywintypes.com_error) as exc:
                    raise AcadError(t("That change does not apply to {k}: {e}",
                                      "Essa alteração não se aplica a {k}: {e}", k=typ, e=exc)) from None
            return entity_info(e, detail=True)

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Explode", "Explodir"), pt=(
        "Explode blocos, polilinhas, cotas ou hachuras em partes simples. Devolve os handles das partes."))
    def explode_entities(
        handles: Handles,
        keep_original: bool = False,
    ) -> dict[str, Any]:
        """Explodes blocks, polylines, dimensions or hatches into simple parts. Returns the parts' handles."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            ents = many_by_handle(doc, handles)
            new: list[str] = []
            with undo_group(doc):
                for e in ents:
                    try:
                        parts = e.Explode()
                    except pywintypes.com_error as exc:
                        raise AcadError(t("Cannot explode {k} ({h}): {e}", "Não dá para explodir {k} ({h}): {e}",
                                          k=short_type(e.ObjectName), h=e.Handle, e=exc.args[1])) from None
                    new += [p.Handle for p in parts]
                    if not keep_original:
                        e.Delete()
            return {"new_handles": new, "created": len(new)}

        return ctx.doc(f)
