"""Engineering objects: walls with doors and windows, and pipes in single or double line.

The geometry is computed in acad_mcp.geometry (pure, tested without AutoCAD); here it is drawn, tagged and its
definition stored in the drawing (acad/store.py), which is what lets add_opening redraw a wall later.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pywintypes
from pydantic import Field

from ..acad import store
from ..acad.errors import AcadError
from ..acad.util import apply_props, by_handle, ensure_linetype, objs, rnd, undo_group
from ..geometry import pipes as PG
from ..geometry import walls as WG
from ..i18n import t
from .common import ADD, EDIT, RO, Color, Ctx, Layer, Pt
from .draw import mk_arc, mk_line, mk_polyline

Path = Annotated[list[Pt], Field(min_length=2, description=t(
    "Points [x, y] in order.", "Pontos [x, y] em ordem."))]


# ------------------------------------------------------------------ helpers


def _erase(doc: Any, handles: list[str]) -> int:
    """Erase what still exists (the user may have erased some already)."""
    gone = 0
    for h in handles:
        try:
            by_handle(doc, h).Delete()
            gone += 1
        except (AcadError, pywintypes.com_error):
            pass
    return gone


def _missing(doc: Any, handles: list[str]) -> list[str]:
    out = []
    for h in handles:
        try:
            by_handle(doc, h)
        except (AcadError, pywintypes.com_error):
            out.append(h)
    return out


def _load(doc: Any, obj_id: str, kind: str | None = None) -> dict[str, Any]:
    rec = store.load(doc, obj_id)
    if rec is None or (kind and rec.get("type") != kind):
        letter = {"wall": "W", "pipe": "P"}.get(kind or "")
        raise AcadError(t("There is no {k} '{i}' in this drawing. Available: {a}",
                          "Não existe {k} '{i}' neste desenho. Disponíveis: {a}",
                          k=kind or "object", i=obj_id, a=store.ids(doc, letter) or "-"))
    return rec


def draw_wall_geometry(app: Any, doc: Any, geo: dict[str, Any], rec: dict[str, Any], created: list[Any]) -> tuple[
        list[str], list[str], list[str]]:
    """Outlines (one closed polyline per ring) and, when asked, one hatch per piece of wall with its holes."""
    sp = doc.ModelSpace
    notes: list[str] = []
    outlines: list[str] = []
    hatches: list[str] = []

    def add(ent: Any) -> Any:
        created.append(ent)
        for n in apply_props(app, doc, ent, rec.get("layer"), rec.get("color")):
            if n not in notes:
                notes.append(n)
        store.tag(doc, ent, rec["id"])
        return ent

    for poly in geo["polygons"]:
        outer = add(mk_polyline(sp, poly["outer"], closed=True))
        holes = [add(mk_polyline(sp, ring, closed=True)) for ring in poly["holes"]]
        outlines += [outer.Handle, *[h.Handle for h in holes]]
        if rec.get("hatch"):
            try:
                h = sp.AddHatch(1, str(rec["hatch"]).upper(), True)
                created.append(h)
                h.AppendOuterLoop(objs([outer]))
                for ring in holes:
                    h.AppendInnerLoop(objs([ring]))
                if str(rec["hatch"]).upper() != "SOLID":
                    h.PatternScale = float(rec.get("hatch_scale") or 1.0)
                h.Evaluate()
            except pywintypes.com_error as exc:
                raise AcadError(t("Could not hatch the wall with pattern '{p}' (does it exist in acad.pat?): {e}",
                                  "Não consegui hachurar a parede com o padrão '{p}' (ele existe em acad.pat?): {e}",
                                  p=rec["hatch"], e=exc.args[1])) from None
            add(h)
            hatches.append(h.Handle)
    return outlines, hatches, notes


def wall_summary(rec: dict[str, Any], geo: dict[str, Any]) -> dict[str, Any]:
    return {"wall_id": rec["id"], "thickness": rec["thickness"], "justification": rec["justification"],
            "length": rnd(geo["length"], 4), "area": rnd(geo["area"], 4), "pieces": len(geo["polygons"]),
            "openings": len(rec["openings"])}


def _rollback(created: list[Any]) -> None:
    for ent in reversed(created):
        try:
            ent.Delete()
        except pywintypes.com_error:
            pass


# ------------------------------------------------------------------ tools


def register(ctx: Ctx) -> None:
    @ctx.tool(ADD, title=t("Draw walls", "Desenhar paredes"), pt=(
        "Desenha paredes a partir dos eixos e da espessura, já com os encontros resolvidos (cantos em L, T e "
        "cruzamentos) e as pontas livres em esquadro. Cada eixo é uma lista de pontos; repita o primeiro ponto no "
        "fim para fechar um cômodo. Devolve o wall_id (use em add_opening para portas e janelas), o comprimento "
        "pelo meio da parede e a área em planta. Um cômodo fechado sai como dois contornos: o de fora e o de dentro."))
    def draw_walls(
        axes: Annotated[list[Path], Field(min_length=1, description=t(
            "Wall axes: a list of polylines, each a list of points. Repeat the first point at the end to close a "
            "room. Axes may touch or cross: the junctions are solved.",
            "Eixos das paredes: lista de polilinhas, cada uma uma lista de pontos. Repita o primeiro ponto no fim "
            "para fechar um cômodo. Os eixos podem se tocar ou cruzar: os encontros são resolvidos."))],
        thickness: Annotated[float, Field(gt=0, description=t("Wall thickness, in drawing units.",
                                                              "Espessura da parede, nas unidades do desenho."))],
        justification: Annotated[Literal["center", "left", "right"], Field(description=t(
            "'center': the axis is the middle of the wall. 'left' / 'right': the axis is one face and the wall "
            "lies on that side of it (looking from each point to the next). For a room given by its INNER "
            "measurements drawn counter-clockwise use 'right'; by its OUTER measurements, 'left'. With left/right "
            "give each run of wall as one continuous axis.",
            "'center': o eixo é o meio da parede. 'left' / 'right': o eixo é uma face e a parede fica desse lado "
            "(olhando de cada ponto para o próximo). Para um cômodo dado pelas medidas INTERNAS, desenhado no "
            "sentido anti-horário, use 'right'; pelas medidas EXTERNAS, 'left'. Com left/right, passe cada trecho "
            "de parede como um eixo contínuo."))] = "center",
        hatch: Annotated[str | None, Field(description=t(
            "Hatch pattern to fill the walls (e.g. ANSI31, SOLID). Empty = no hatch.",
            "Padrão de hachura para preencher as paredes (ex.: ANSI31, SOLID). Vazio = sem hachura."))] = None,
        hatch_scale: Annotated[float, Field(gt=0)] = 1.0,
        layer: Layer = None, color: Color = None,
    ) -> dict[str, Any]:
        """Draws walls from their axes and a thickness, with the junctions solved (L corners, T junctions and
        crossings) and free ends cut square. Each axis is a list of points; repeat the first point at the end to
        close a room. Returns the wall_id (use it in add_opening for doors and windows), the length along the
        middle of the wall and the plan area. A closed room comes out as two outlines: outer and inner."""
        geo = WG.build_walls(axes, thickness, justification)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            created: list[Any] = []
            with undo_group(doc):
                rec: dict[str, Any] = {
                    "type": "wall", "id": store.next_id(doc, "W"),
                    "axes": [[[float(p[0]), float(p[1])] for p in axis] for axis in axes],
                    "thickness": float(thickness), "justification": justification, "layer": layer, "color": color,
                    "hatch": hatch, "hatch_scale": float(hatch_scale), "openings": [],
                }
                try:
                    outlines, hatches, notes = draw_wall_geometry(app, doc, geo, rec, created)
                    rec["handles"] = {"outline": outlines, "hatch": hatches}
                    store.save(doc, rec["id"], rec)
                except Exception:
                    _rollback(created)
                    raise
            out = {**wall_summary(rec, geo), "outlines": outlines}
            if hatches:
                out["hatches"] = hatches
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Door, window or gap in a wall", "Porta, janela ou vão na parede"), pt=(
        "Abre um vão numa parede feita por draw_walls e desenha o símbolo: porta (folha aberta a 90° e o arco), "
        "janela (moldura e linha do vidro) ou só o vão. position é o CENTRO da abertura, um ponto sobre a parede; "
        "a parede é redesenhada com o recorte. hinge e swing valem para porta: hinge = lado da dobradiça ao longo "
        "do eixo da parede ('start' = mais perto do início do trecho, 'end' = do fim); swing = lado para onde "
        "abre ('left' ou 'right', olhando do início para o fim do trecho). Confira com get_view_image."))
    def add_opening(
        wall_id: Annotated[str, Field(description=t("Id returned by draw_walls (e.g. 'W1').",
                                                    "Id devolvido por draw_walls (ex.: 'W1')."))],
        kind: Literal["door", "window", "gap"],
        position: Annotated[Pt, Field(description=t(
            "Centre of the opening: a point on the wall (anywhere across its thickness).",
            "Centro da abertura: um ponto sobre a parede (em qualquer lugar da espessura)."))],
        width: Annotated[float, Field(gt=0, description=t("Width of the opening along the wall.",
                                                          "Largura da abertura ao longo da parede."))],
        hinge: Annotated[Literal["start", "end"], Field(description=t(
            "Door only: which end of the opening has the hinge, along the wall axis.",
            "Só porta: em qual ponta da abertura fica a dobradiça, ao longo do eixo da parede."))] = "start",
        swing: Annotated[Literal["left", "right"], Field(description=t(
            "Door only: side of the wall it opens to, looking along the axis from its start.",
            "Só porta: lado da parede para onde abre, olhando ao longo do eixo a partir do início."))] = "left",
        layer: Annotated[str | None, Field(description=t(
            "Layer of the door / window symbol. Empty = the wall's layer.",
            "Layer do símbolo da porta / janela. Vazio = o layer da parede."))] = None,
    ) -> dict[str, Any]:
        """Cuts an opening in a wall made by draw_walls and draws its symbol: a door (leaf open at 90 degrees and
        its arc), a window (frame and glass line) or just the gap. position is the CENTRE of the opening, a point
        on the wall; the wall is redrawn with the cut. hinge and swing are for doors: hinge = which end of the
        opening along the wall axis ('start' = nearer the start of that stretch, 'end' = nearer its end); swing =
        the side it opens to ('left' or 'right', looking from the start of the stretch to its end). Check the
        result with get_view_image."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            rec = _load(doc, wall_id, "wall")
            thick = rec["thickness"]
            lines = WG.centerlines(rec["axes"], thick, rec["justification"])
            loc = WG.locate_opening(lines, position, width, thick)
            a, b = loc["a"], loc["b"]
            existing = [(o["a"], o["b"]) for o in rec["openings"]]
            if WG.overlaps_opening(a, b, thick, existing):
                raise AcadError(t("That opening overlaps another opening of wall {w} (see get_wall).",
                                  "Essa abertura se sobrepõe a outra abertura da parede {w} (veja get_wall).",
                                  w=rec["id"]))
            geo = WG.build_walls(rec["axes"], thick, rec["justification"], existing + [(a, b)])
            sym_layer = layer or rec.get("layer")
            sp = doc.ModelSpace
            created: list[Any] = []
            with undo_group(doc):
                try:
                    outlines, hatches, notes = draw_wall_geometry(app, doc, geo, rec, created)
                    symbol: list[Any] = []
                    if kind == "door":
                        d = WG.door_symbol(a, b, thick, hinge, swing)
                        arc = d["arc"]
                        symbol = [mk_line(sp, d["leaf"][0], d["leaf"][1]),
                                  mk_arc(sp, arc["center"], arc["radius"], arc["start_angle_deg"], arc["end_angle_deg"])]
                    elif kind == "window":
                        w = WG.window_symbol(a, b, thick)
                        symbol = [mk_polyline(sp, w["frame"], closed=True), mk_line(sp, w["glass"][0], w["glass"][1])]
                    created += symbol
                    for ent in symbol:
                        for n in apply_props(app, doc, ent, sym_layer):
                            if n not in notes:
                                notes.append(n)
                        store.tag(doc, ent, rec["id"])
                    old = rec.get("handles", {})
                    _erase(doc, list(old.get("hatch", [])) + list(old.get("outline", [])))
                    nums = [int(o["id"][1:]) for o in rec["openings"] if str(o.get("id", ""))[1:].isdigit()]
                    opening = {"id": f"O{max(nums, default=0) + 1}", "kind": kind, "a": a, "b": b,
                               "width": float(width), "handles": [e.Handle for e in symbol]}
                    if kind == "door":
                        opening.update(hinge=hinge, swing=swing)
                    rec["openings"].append(opening)
                    rec["handles"] = {"outline": outlines, "hatch": hatches}
                    store.save(doc, rec["id"], rec)
                except Exception:
                    _rollback(created)
                    raise
            out = {"wall_id": rec["id"], "opening_id": opening["id"], "kind": kind, "start": a, "end": b,
                   "center": [rnd((a[0] + b[0]) / 2), rnd((a[1] + b[1]) / 2)], "width": float(width),
                   "symbol": opening["handles"], "wall": {**wall_summary(rec, geo), "outlines": outlines}}
            if kind == "door":
                out.update(hinge=hinge, swing=swing)
            if hatches:
                out["wall"]["hatches"] = hatches
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)

    @ctx.tool(RO, title=t("Wall and pipe data", "Dados de paredes e tubos"), pt=(
        "Sem wall_id: lista as paredes e os tubos que este servidor desenhou neste desenho (id, medidas). Com um "
        "id (parede 'W1' ou tubo 'P1'): a definição completa — eixos, espessura, aberturas com suas posições, "
        "comprimento, área — e se alguma entidade foi apagada à mão."))
    def get_wall(
        wall_id: Annotated[str | None, Field(description=t(
            "Wall id ('W1') or pipe id ('P1'). Empty = list everything.",
            "Id da parede ('W1') ou do tubo ('P1'). Vazio = listar tudo."))] = None,
    ) -> dict[str, Any]:
        """Without wall_id: lists the walls and pipes this server drew in this drawing (id, sizes). With an id
        (wall 'W1' or pipe 'P1'): the full definition - axes, thickness, openings with their positions, length,
        area - and whether any of its entities was erased by hand."""

        def wall_data(rec: dict[str, Any]) -> dict[str, Any]:
            geo = WG.build_walls(rec["axes"], rec["thickness"], rec["justification"],
                                 [(o["a"], o["b"]) for o in rec["openings"]])
            return wall_summary(rec, geo)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            if not wall_id:
                walls = [wall_data(r) for r in (store.load(doc, i) for i in store.ids(doc, "W")) if r]
                pipes = [{"pipe_id": r["id"], "mode": r["mode"], "diameter": r["diameter"],
                          "length": rnd(r["length"], 4), "elbows": r["elbows"]}
                         for r in (store.load(doc, i) for i in store.ids(doc, "P")) if r]
                return {"walls": walls, "pipes": pipes}
            rec = _load(doc, wall_id)
            handles = rec.get("handles", {})
            flat = [h for group in handles.values() for h in group]
            if rec["type"] == "wall":
                flat += [h for o in rec["openings"] for h in o.get("handles", [])]
                out = {**wall_data(rec), "axes": rec["axes"], "layer": rec.get("layer"), "hatch": rec.get("hatch"),
                       "openings_list": [{k: v for k, v in o.items() if k != "handles"} | {"symbol": o.get("handles", [])}
                                         for o in rec["openings"]],
                       "outlines": handles.get("outline", []), "hatches": handles.get("hatch", [])}
            else:
                out = {"pipe_id": rec["id"], **{k: rec[k] for k in ("mode", "diameter", "elbow_radius", "elbows", "path")},
                       "length": rnd(rec["length"], 4), "layer": rec.get("layer"), "handles": handles}
            gone = _missing(doc, flat)
            if gone:
                out["erased_entities"] = gone
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Draw pipe", "Desenhar tubulação"), pt=(
        "Desenha um trecho de tubulação por um caminho de pontos. mode='single' (unifilar): uma polilinha pelo "
        "eixo. mode='double' (bifilar): as duas paredes do tubo no diâmetro dado, num contorno fechado, com a "
        "linha de centro. As curvas saem com o raio pedido (elbow_radius, medido no eixo); sem ele, canto vivo no "
        "unifilar e 1,5 × diâmetro no bifilar. Devolve o comprimento pelo eixo, já com as curvas. Não desenha "
        "conexões (tê, flange, válvula)."))
    def draw_pipe(
        path: Path,
        diameter: Annotated[float, Field(gt=0, description=t("Outside diameter, in drawing units.",
                                                             "Diâmetro externo, nas unidades do desenho."))],
        mode: Annotated[Literal["single", "double"], Field(description=t(
            "'single' = one line along the axis; 'double' = both walls of the pipe.",
            "'single' = uma linha pelo eixo (unifilar); 'double' = as duas paredes do tubo (bifilar)."))] = "single",
        elbow_radius: Annotated[float | None, Field(ge=0, description=t(
            "Radius of the bends on the axis. Empty = 0 (sharp) for single, 1.5 x diameter for double. In double "
            "it must be 0 or more than half the diameter.",
            "Raio das curvas no eixo. Vazio = 0 (canto vivo) no unifilar, 1,5 × diâmetro no bifilar. No bifilar "
            "precisa ser 0 ou maior que meio diâmetro."))] = None,
        centerline: Annotated[bool, Field(description=t(
            "Double line only: also draw the axis with the CENTER linetype.",
            "Só bifilar: desenhar também o eixo com o tipo de linha CENTER."))] = True,
        layer: Layer = None, color: Color = None,
    ) -> dict[str, Any]:
        """Draws a pipe run along a path of points. mode='single': one polyline along the axis. mode='double': both
        walls of the pipe at the given diameter, as one closed outline, plus the centre line. Bends get the given
        radius (elbow_radius, measured on the axis); without it, sharp corners in single line and 1.5 x diameter
        in double line. Returns the length along the axis, bends included. Fittings (tee, flange, valve) are not
        drawn."""
        geo = PG.pipe_geometry(path, diameter, mode, elbow_radius)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            sp = doc.ModelSpace
            created: list[Any] = []
            notes: list[str] = []
            handles: dict[str, list[str]] = {}
            with undo_group(doc):
                pid = store.next_id(doc, "P")
                try:
                    center = geo["centerline"]
                    if mode == "double":
                        o = geo["outline"]
                        ent = mk_polyline(sp, o["points"], closed=True, bulges=o["bulges"])
                        created.append(ent)
                        notes += apply_props(app, doc, ent, layer, color)
                        handles["outline"] = [ent.Handle]
                    if mode == "single" or centerline:
                        ent = mk_polyline(sp, center["points"], closed=False, bulges=center["bulges"])
                        created.append(ent)
                        notes += [n for n in apply_props(app, doc, ent, layer, color) if n not in notes]
                        if mode == "double":
                            ensure_linetype(doc, "CENTER")
                            ent.Linetype = "CENTER"
                        handles["centerline"] = [ent.Handle]
                    for ent in created:
                        store.tag(doc, ent, pid)
                    store.save(doc, pid, {
                        "type": "pipe", "id": pid, "path": [[float(p[0]), float(p[1])] for p in path],
                        "diameter": float(diameter), "mode": mode, "elbow_radius": geo["elbow_radius"],
                        "elbows": geo["elbows"], "length": geo["length"], "layer": layer, "handles": handles})
                except Exception:
                    _rollback(created)
                    raise
            out = {"pipe_id": pid, "mode": mode, "diameter": float(diameter), "elbow_radius": rnd(geo["elbow_radius"]),
                   "elbows": geo["elbows"], "length": rnd(geo["length"], 4),
                   "straight_length": rnd(geo["straight_length"], 4), **handles}
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)
