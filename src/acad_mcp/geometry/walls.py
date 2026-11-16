"""Walls: axes + thickness -> closed outlines with clean L, T and X junctions; doors and windows cut them.

How: every axis segment becomes a rectangle (the wall body, square ends), every place where two segments meet
gets a mitred corner piece, and everything is merged (shapely). An opening is a rectangle removed from the body.
A room surrounded by walls comes out as one polygon with a hole, i.e. an outer and an inner outline.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from ..acad.errors import AcadError
from ..i18n import t

Pt = tuple[float, float]
Line = tuple[list[Pt], bool]  # points, closed

MITRE = 5.0   # corners sharper than about 23 degrees are cut (bevelled) instead of growing a long spike
ROUND = 6     # decimals kept in the result (drawing units)


def _unit(a: Pt, b: Pt) -> Pt:
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _dist(a: Pt, b: Pt) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def clean_axes(axes: Iterable[Sequence[Sequence[float]]]) -> list[Line]:
    """2D points, repeated points dropped; an axis that ends where it starts is closed."""
    out: list[Line] = []
    for i, axis in enumerate(axes):
        pts: list[Pt] = []
        for p in axis:
            q = (float(p[0]), float(p[1]))
            if not pts or _dist(pts[-1], q) > 1e-9:
                pts.append(q)
        closed = len(pts) > 2 and _dist(pts[0], pts[-1]) <= 1e-9
        if closed:
            pts.pop()
        if len(pts) < 2 or (closed and len(pts) < 3):
            raise AcadError(t("Axis {i} needs at least 2 different points.",
                              "O eixo {i} precisa de pelo menos 2 pontos diferentes.", i=i + 1))
        out.append((pts, closed))
    if not out:
        raise AcadError(t("Give at least one axis.", "Informe pelo menos um eixo."))
    return out


def centerlines(axes: Iterable[Sequence[Sequence[float]]], thickness: float,
                justification: str = "center") -> list[Line]:
    """The line in the middle of each wall. 'center': the axis itself. 'left' / 'right': the wall lies entirely
    on that side of the axis (looking from its first point to the next), so its middle is half a thickness away."""
    lines = clean_axes(axes)
    if justification == "center":
        return lines
    if justification not in ("left", "right"):
        raise AcadError(t("justification must be center, left or right.",
                          "justification deve ser center, left ou right."))
    half = thickness / 2.0
    out: list[Line] = []
    for i, (pts, closed) in enumerate(lines):
        if closed:
            poly = Polygon(pts)
            if not poly.is_valid:
                raise AcadError(t("Axis {i} crosses itself.", "O eixo {i} cruza a si mesmo.", i=i + 1))
            inside = (justification == "left") == poly.exterior.is_ccw
            g = poly.buffer(-half if inside else half, join_style="mitre", mitre_limit=MITRE)
            if g.is_empty or not isinstance(g, Polygon):
                raise AcadError(t("Axis {i}: the wall is too thick for this outline.",
                                  "Eixo {i}: a parede é grossa demais para este contorno.", i=i + 1))
            ring = [(float(x), float(y)) for x, y in list(g.exterior.coords)[:-1]]
            # keep the direction and the starting corner of the axis as it was given: the hinge and swing side of
            # a door are told relative to it (the buffer returns its ring in whatever order it likes)
            if g.exterior.is_ccw != poly.exterior.is_ccw:
                ring.reverse()
            first = min(range(len(ring)), key=lambda k: _dist(ring[k], pts[0]))
            out.append((ring[first:] + ring[:first], True))
        else:
            g = LineString(pts).offset_curve(half if justification == "left" else -half, join_style="mitre",
                                             mitre_limit=MITRE)
            parts = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
            parts = [p for p in parts if not p.is_empty and len(p.coords) >= 2]
            if not parts:
                raise AcadError(t("Axis {i}: could not offset it by half the thickness.",
                                  "Eixo {i}: não consegui deslocá-lo de meia espessura.", i=i + 1))
            for p in parts:
                out.append(([(float(x), float(y)) for x, y in p.coords], False))
    return out


def _segments(lines: list[Line]) -> list[tuple[Pt, Pt]]:
    segs = []
    for pts, closed in lines:
        seq = pts + [pts[0]] if closed else pts
        for a, b in zip(seq, seq[1:]):
            if _dist(a, b) > 1e-9:
                segs.append((a, b))
    return segs


def lines_length(lines: list[Line]) -> float:
    return sum(_dist(a, b) for a, b in _segments(lines))


def _body(lines: list[Line], thickness: float) -> Any:
    half = thickness / 2.0
    parts = []
    nodes: dict[tuple[float, float], list[tuple[Pt, Pt]]] = defaultdict(list)
    for a, b in _segments(lines):
        parts.append(LineString([a, b]).buffer(half, cap_style="flat"))
        nodes[(round(a[0], ROUND), round(a[1], ROUND))].append((a, b))
        nodes[(round(b[0], ROUND), round(b[1], ROUND))].append((b, a))
    for incident in nodes.values():
        for i in range(len(incident)):
            for j in range(i + 1, len(incident)):
                (n, p), (_, q) = incident[i], incident[j]
                up, uq = _unit(n, p), _unit(n, q)
                if up[0] * uq[0] + up[1] * uq[1] > 0.99985:  # same direction (under 1 degree): overlapping walls
                    continue
                lp, lq = min(_dist(n, p), thickness), min(_dist(n, q), thickness)
                stub = LineString([(n[0] + up[0] * lp, n[1] + up[1] * lp), n, (n[0] + uq[0] * lq, n[1] + uq[1] * lq)])
                parts.append(stub.buffer(half, cap_style="flat", join_style="mitre", mitre_limit=MITRE))
    return unary_union(parts)


def opening_cut(a: Pt, b: Pt, thickness: float) -> Any:
    """Rectangle removed from the wall: from a to b along the wall, a hair wider than the wall across it."""
    return LineString([a, b]).buffer(thickness * 0.505, cap_style="flat")


def _rings(geom: Any, tol: float) -> list[dict[str, Any]]:
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    out = []
    for poly in polys:
        if not isinstance(poly, Polygon) or poly.is_empty or poly.area <= tol * tol:
            continue

        def ring(coords: Any) -> list[list[float]]:
            return [[round(float(x), ROUND), round(float(y), ROUND)] for x, y in list(coords)[:-1]]

        out.append({"outer": ring(poly.exterior.coords), "holes": [ring(r.coords) for r in poly.interiors],
                    "area": float(poly.area)})
    out.sort(key=lambda p: (-p["area"], p["outer"][0]))
    return out


def build_walls(axes: Iterable[Sequence[Sequence[float]]], thickness: float, justification: str = "center",
                openings: Iterable[tuple[Sequence[float], Sequence[float]]] = ()) -> dict[str, Any]:
    """Outlines of the walls. openings: (a, b) points on a wall's centre line, each a gap from a to b.

    Returns {"polygons": [{"outer": [[x, y]...], "holes": [[[x, y]...]...], "area"}], "area", "length",
    "centerlines": [{"points", "closed"}]}; length is measured along the middle of the walls.
    """
    if not thickness > 0:
        raise AcadError(t("Thickness must be greater than zero.", "A espessura precisa ser maior que zero."))
    lines = centerlines(axes, thickness, justification)
    body = _body(lines, thickness)
    eps = thickness * 1e-6
    body = body.buffer(eps, join_style="mitre", mitre_limit=MITRE).buffer(-eps, join_style="mitre", mitre_limit=MITRE)
    cuts = [opening_cut((float(a[0]), float(a[1])), (float(b[0]), float(b[1])), thickness) for a, b in openings]
    if cuts:
        body = body.difference(unary_union(cuts))
    body = body.simplify(eps * 10)
    polys = _rings(body, eps)
    if not polys:
        raise AcadError(t("These axes and thickness give no wall.", "Estes eixos e espessura não geram parede."))
    return {
        "polygons": polys,
        "area": sum(p["area"] for p in polys),
        "length": lines_length(lines),
        "centerlines": [{"points": [[round(x, ROUND), round(y, ROUND)] for x, y in pts], "closed": closed}
                        for pts, closed in lines],
    }


def locate_opening(lines: list[Line], position: Sequence[float], width: float, thickness: float) -> dict[str, Any]:
    """Where an opening of `width`, centred on `position`, sits on the nearest wall stretch.

    Returns {"a", "b"} (its two ends on the centre line, in the direction of the axis), "direction" (unit vector
    from a to b) and "normal" (unit vector to the left of that direction).
    """
    if not width > 0:
        raise AcadError(t("Width must be greater than zero.", "A largura precisa ser maior que zero."))
    p = (float(position[0]), float(position[1]))
    best: tuple[float, Pt, Pt, float] | None = None
    for a, b in _segments(lines):
        u = _unit(a, b)
        s = (p[0] - a[0]) * u[0] + (p[1] - a[1]) * u[1]
        length = _dist(a, b)
        sc = min(max(s, 0.0), length)
        d = _dist(p, (a[0] + u[0] * sc, a[1] + u[1] * sc))
        if best is None or d < best[0] - 1e-9:
            best = (d, a, b, sc)
    assert best is not None
    d, a, b, s = best
    if d > thickness:
        raise AcadError(t("position is {d} away from the nearest wall centre line: give a point on the wall.",
                          "position está a {d} do eixo de parede mais próximo: informe um ponto sobre a parede.",
                          d=round(d, 3)))
    u = _unit(a, b)
    length = _dist(a, b)
    lo, hi = s - width / 2.0, s + width / 2.0
    tol = 1e-6
    if lo < -tol or hi > length + tol:
        raise AcadError(t(
            "The opening does not fit: this wall stretch is {l} long (from {a} to {b}) and an opening {w} wide "
            "centred there would go from {lo} to {hi} along it. Move position or reduce the width.",
            "A abertura não cabe: este trecho de parede tem {l} de comprimento (de {a} a {b}) e uma abertura de {w} "
            "centrada ali iria de {lo} a {hi} ao longo dele. Mude position ou reduza a largura.",
            l=round(length, 3), a=[round(a[0], 3), round(a[1], 3)], b=[round(b[0], 3), round(b[1], 3)],
            w=width, lo=round(lo, 3), hi=round(hi, 3)))
    lo, hi = max(lo, 0.0), min(hi, length)
    pa = (round(a[0] + u[0] * lo, ROUND), round(a[1] + u[1] * lo, ROUND))
    pb = (round(a[0] + u[0] * hi, ROUND), round(a[1] + u[1] * hi, ROUND))
    return {"a": list(pa), "b": list(pb), "direction": [u[0], u[1]], "normal": [-u[1], u[0]]}


def overlaps_opening(a: Sequence[float], b: Sequence[float], thickness: float,
                     existing: Iterable[tuple[Sequence[float], Sequence[float]]]) -> bool:
    new = opening_cut((a[0], a[1]), (b[0], b[1]), thickness)
    return any(new.intersection(opening_cut((x[0], x[1]), (y[0], y[1]), thickness)).area > thickness * thickness * 1e-6
               for x, y in existing)


def _normal(a: Sequence[float], b: Sequence[float]) -> Pt:
    u = _unit((a[0], a[1]), (b[0], b[1]))
    return (-u[1], u[0])


def door_symbol(a: Sequence[float], b: Sequence[float], thickness: float, hinge: str = "start",
                swing: str = "left") -> dict[str, Any]:
    """Plan symbol of a door open at 90 degrees: the leaf (a line as long as the opening, square to the wall) and
    the arc of its swing. hinge: 'start' = at a, 'end' = at b. swing: side of the wall it opens to, 'left' or
    'right' of the direction a -> b. The leaf starts on that face of the wall."""
    n = _normal(a, b)
    s = 1.0 if swing == "left" else -1.0
    ns = (n[0] * s, n[1] * s)
    half = thickness / 2.0
    w = _dist((a[0], a[1]), (b[0], b[1]))
    h, o = (a, b) if hinge == "start" else (b, a)
    hf = (h[0] + ns[0] * half, h[1] + ns[1] * half)
    of = (o[0] + ns[0] * half, o[1] + ns[1] * half)
    tip = (hf[0] + ns[0] * w, hf[1] + ns[1] * w)
    a1 = math.degrees(math.atan2(tip[1] - hf[1], tip[0] - hf[0])) % 360
    a2 = math.degrees(math.atan2(of[1] - hf[1], of[0] - hf[0])) % 360
    start, end = (a1, a2) if abs(((a2 - a1) % 360) - 90) < 1e-6 else (a2, a1)
    r = lambda v: round(v, ROUND)  # noqa: E731
    return {"leaf": [[r(hf[0]), r(hf[1])], [r(tip[0]), r(tip[1])]],
            "arc": {"center": [r(hf[0]), r(hf[1])], "radius": r(w), "start_angle_deg": r(start), "end_angle_deg": r(end)}}


def window_symbol(a: Sequence[float], b: Sequence[float], thickness: float) -> dict[str, Any]:
    """Plan symbol of a window: the frame (the gap closed on both faces of the wall) and the glass line in the
    middle."""
    n = _normal(a, b)
    half = thickness / 2.0
    r = lambda v: round(v, ROUND)  # noqa: E731
    frame = [[r(a[0] + n[0] * half), r(a[1] + n[1] * half)], [r(b[0] + n[0] * half), r(b[1] + n[1] * half)],
             [r(b[0] - n[0] * half), r(b[1] - n[1] * half)], [r(a[0] - n[0] * half), r(a[1] - n[1] * half)]]
    return {"frame": frame, "glass": [[r(a[0]), r(a[1])], [r(b[0]), r(b[1])]]}
