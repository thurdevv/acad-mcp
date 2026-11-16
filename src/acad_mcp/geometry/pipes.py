"""Pipes: a path + diameter + elbow radius -> the centre line with arcs (single line) and, for double line,
one closed outline made of the two walls of the pipe and its square ends.

Arcs are stored the AutoCAD way, as a "bulge" on the vertex where the arc starts:
bulge = tan(swept angle / 4), positive when the arc turns left (counter-clockwise).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..acad.errors import AcadError
from ..i18n import t

Pt = tuple[float, float]
ROUND = 6


def _clean(path: Sequence[Sequence[float]]) -> list[Pt]:
    pts: list[Pt] = []
    for p in path:
        q = (float(p[0]), float(p[1]))
        if not pts or math.hypot(q[0] - pts[-1][0], q[1] - pts[-1][1]) > 1e-9:
            pts.append(q)
    if len(pts) < 2:
        raise AcadError(t("The path needs at least 2 different points.",
                          "O caminho precisa de pelo menos 2 pontos diferentes."))
    return pts


def pipe_geometry(path: Sequence[Sequence[float]], diameter: float, mode: str = "single",
                  elbow_radius: float | None = None) -> dict[str, Any]:
    """Geometry of a pipe run.

    elbow_radius is measured on the centre line. None = 0 (sharp corners) for single line and 1.5 x diameter
    (a long-radius elbow) for double line. In double line it must be 0 or greater than half the diameter.

    Returns {"centerline": {"points", "bulges"}, "outline": {"points", "bulges"} or None, "length" (along the
    centre line, arcs included), "elbows", "elbow_radius", "straight_length", "arc_length"}.
    """
    if not diameter > 0:
        raise AcadError(t("Diameter must be greater than zero.", "O diâmetro precisa ser maior que zero."))
    if mode not in ("single", "double"):
        raise AcadError(t("mode must be single or double.", "mode deve ser single ou double."))
    pts = _clean(path)
    r = (1.5 * diameter if mode == "double" else 0.0) if elbow_radius is None else float(elbow_radius)
    if r < 0:
        raise AcadError(t("Elbow radius cannot be negative.", "O raio da curva não pode ser negativo."))
    half = diameter / 2.0
    if mode == "double" and 0 < r <= half:
        raise AcadError(t(
            "In double line the elbow radius must be 0 (sharp corner) or greater than half the diameter ({h}): "
            "with {r} the inner wall of the pipe would have no radius left.",
            "No bifilar o raio da curva precisa ser 0 (canto vivo) ou maior que meio diâmetro ({h}): com {r} a "
            "parede interna do tubo ficaria sem raio.", h=half, r=r))

    dirs = []
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        dirs.append(((b[0] - a[0]) / d, (b[1] - a[1]) / d, d))
    # corner data at each interior point: deflection angle, turn sign, tangent length
    corners: dict[int, tuple[float, float, float]] = {}
    for i in range(1, len(pts) - 1):
        (ux, uy, _), (vx, vy, _) = dirs[i - 1], dirs[i]
        cross, dot = ux * vy - uy * vx, ux * vx + uy * vy
        theta = math.atan2(abs(cross), dot)
        if theta < 1e-9:
            continue  # straight on
        if theta > math.pi - 1e-6:
            raise AcadError(t("The path turns back on itself at point {i}.",
                              "O caminho volta sobre si mesmo no ponto {i}.", i=i + 1))
        corners[i] = (theta, 1.0 if cross > 0 else -1.0, r * math.tan(theta / 2.0))
    for k, (_, _, length) in enumerate(dirs):
        need = corners.get(k, (0, 0, 0.0))[2] + corners.get(k + 1, (0, 0, 0.0))[2]
        if need > length + 1e-9:
            raise AcadError(t(
                "Elbow radius {r} does not fit between points {a} and {b}: that run is {l} long and the curves "
                "need {n}. Use a smaller radius or a longer run.",
                "O raio de curva {r} não cabe entre os pontos {a} e {b}: esse trecho tem {l} e as curvas precisam "
                "de {n}. Use um raio menor ou um trecho maior.",
                r=r, a=k + 1, b=k + 2, l=round(length, 3), n=round(need, 3)))

    # stations along the centre line: point, bulge to the next station, and how to offset it sideways
    st: list[dict[str, Any]] = []

    def normal(k: int) -> Pt:
        return (-dirs[k][1], dirs[k][0])

    st.append({"p": pts[0], "bulge": 0.0, "off": normal(0)})
    arc_length = 0.0
    for i in range(1, len(pts) - 1):
        if i not in corners:
            continue
        theta, sign, tan_len = corners[i]
        n_in, n_out = normal(i - 1), normal(i)
        if r == 0:
            k = 1.0 + n_in[0] * n_out[0] + n_in[1] * n_out[1]
            st.append({"p": pts[i], "bulge": 0.0, "off": ((n_in[0] + n_out[0]) / k, (n_in[1] + n_out[1]) / k)})
        else:
            (ux, uy, _), (vx, vy, _) = dirs[i - 1], dirs[i]
            st.append({"p": (pts[i][0] - ux * tan_len, pts[i][1] - uy * tan_len),
                       "bulge": sign * math.tan(theta / 4.0), "off": n_in})
            st.append({"p": (pts[i][0] + vx * tan_len, pts[i][1] + vy * tan_len), "bulge": 0.0, "off": n_out})
            arc_length += r * theta
    st.append({"p": pts[-1], "bulge": 0.0, "off": normal(len(dirs) - 1)})

    straight = sum(d[2] for d in dirs) - 2.0 * sum(c[2] for c in corners.values())
    rd = lambda v: round(v, ROUND)  # noqa: E731
    center = {"points": [[rd(s["p"][0]), rd(s["p"][1])] for s in st], "bulges": [rd(s["bulge"]) for s in st]}
    outline = None
    if mode == "double":
        left = [(s["p"][0] + s["off"][0] * half, s["p"][1] + s["off"][1] * half) for s in st]
        right = [(s["p"][0] - s["off"][0] * half, s["p"][1] - s["off"][1] * half) for s in st]
        bul = [s["bulge"] for s in st]
        m = len(st) - 1
        points = left + right[::-1]
        bulges = bul[:m] + [0.0] + [-bul[j - 1] for j in range(m, 0, -1)] + [0.0]
        outline = {"points": [[rd(x), rd(y)] for x, y in points], "bulges": [rd(b) for b in bulges]}
    return {"centerline": center, "outline": outline, "length": straight + arc_length, "straight_length": straight,
            "arc_length": arc_length, "elbows": len(corners), "elbow_radius": r}


def polyline_length(points: Sequence[Sequence[float]], bulges: Sequence[float], closed: bool = False) -> float:
    """Length of a polyline with bulges (used to cross-check the geometry in tests)."""
    n = len(points)
    total = 0.0
    for i in range(n if closed else n - 1):
        a, b = points[i], points[(i + 1) % n]
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        bulge = bulges[i] if i < len(bulges) else 0.0
        if abs(bulge) < 1e-12:
            total += chord
        else:
            ang = 4.0 * math.atan(abs(bulge))
            total += chord / (2.0 * math.sin(ang / 2.0)) * ang
    return total
