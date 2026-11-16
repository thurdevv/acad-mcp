"""Fast in-process reading through the mcp_core.lsp library.

Measured on AutoCAD 2027 with 50,000 entities: COM out-of-process needs ~71 s just to select
and ~26 ms per entity to read details (~22 min); in-process LISP reads everything in about a
minute or less. The library is sent as inline code (not (load ...)), because loading a .lsp
from an untrusted folder raises AutoCAD's SECURELOAD dialog.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from ..i18n import t
from . import commands as C
from .errors import AcadError
from .util import dxf_types, rnd

CORE_PATH = Path(__file__).resolve().parent.parent / "lisp" / "mcp_core.lsp"
CORE_VERSION = "0.4"

TYPE_NAMES = {
    "LINE": "Line", "LWPOLYLINE": "Polyline", "POLYLINE": "2dPolyline", "CIRCLE": "Circle", "ARC": "Arc",
    "TEXT": "Text", "MTEXT": "MText", "INSERT": "BlockReference", "DIMENSION": "Dimension", "HATCH": "Hatch",
    "POINT": "Point", "ELLIPSE": "Ellipse", "SPLINE": "Spline", "MULTILEADER": "MLeader", "ACAD_TABLE": "Table",
    "LEADER": "Leader", "REGION": "Region", "SOLID": "Solid", "IMAGE": "RasterImage", "ATTDEF": "AttributeDefinition",
    "VIEWPORT": "Viewport", "3DSOLID": "3dSolid", "XLINE": "Xline", "RAY": "Ray", "WIPEOUT": "Wipeout",
}


def _core_code() -> str:
    """Library source (comments removed by run_lisp; long code goes through a temp file)."""
    return CORE_PATH.read_text(encoding="utf-8")


def ensure_loaded(app: Any, doc: Any, timeout: float) -> None:
    r = C.run_lisp(app, doc, f'(if (and (boundp (quote mcp:version)) (= mcp:version "{CORE_VERSION}")) "ok" "no")', timeout)
    if r.get("result") == '"ok"':
        return
    r = C.run_lisp(app, doc, _core_code() + ' "loaded"', timeout)
    if r.get("result") != '"loaded"':
        raise AcadError(t("Could not load the fast-read library in AutoCAD: {e}",
                          "Não consegui carregar a biblioteca de leitura rápida no AutoCAD: {e}",
                          e=r.get("error")))


def lisp_filter(types: Sequence[str] | None = None, layers: Sequence[str] | None = None, space: str = "model",
                text_contains: str | None = None, color: int | None = None, layout: str | None = None) -> str:
    parts: list[str] = []
    dt = dxf_types(types)
    if dt:
        parts.append(f"(cons 0 {C.lisp_str(dt)})")
    if layers:
        parts.append(f"(cons 8 {C.lisp_str(','.join(layers))})")
    s = (space or "model").lower()
    if layout:
        parts.append(f"(cons 410 {C.lisp_str(layout)})")
    elif s.startswith("model"):
        parts.append('(cons 410 "Model")')
    elif s.startswith("paper") or s == "layout":
        parts += ['(cons -4 "<NOT")', '(cons 410 "Model")', '(cons -4 "NOT>")']
    if text_contains:
        parts.append(f"(cons 1 {C.lisp_str('*' + text_contains + '*')})")
    if color is not None:
        parts.append(f"(cons 62 {int(color)})")
    return f"(list {' '.join(parts)})" if parts else "nil"


def _color(rec: dict) -> Any:
    if "rgb" in rec:
        v = int(rec["rgb"])
        return f"{(v >> 16) & 255},{(v >> 8) & 255},{v & 255}"
    c = rec.get("c")
    if c is None or c == 256:
        return "ByLayer"
    if c == 0:
        return "ByBlock"
    return c


def to_entity(rec: dict) -> dict[str, Any]:
    """Compact LISP record -> same keys as util.entity_info."""
    typ = TYPE_NAMES.get(rec["t"], rec["t"].title())
    d: dict[str, Any] = {"handle": rec["h"], "type": typ, "layer": rec["l"], "color": _color(rec)}
    if "lay" in rec:
        d["layout"] = rec["lay"]
    if "n" in rec:
        d["name"] = rec.get("en") or rec["n"]
        if rec.get("en"):
            d["anonymous_name"] = rec["n"]
    m = {"a": "start", "b": "end", "ctr": "center", "v": "vertices", "x": "text", "att": "attributes",
         "m": "measurement", "pat": "pattern", "nv": "vertex_count", "cl": "closed"}
    for k, name in m.items():
        if k in rec:
            d[name] = rec[k]
    if "r" in rec:
        d["radius"] = rnd(rec["r"])
    if "p" in rec:
        d["position" if typ == "Point" else "insertion"] = rec["p"]
    if "ht" in rec:
        d["height"] = rnd(rec["ht"])
    if "rot" in rec:
        d["rotation_deg"] = rnd(math.degrees(rec["rot"]) % 360)
    if "sc" in rec:
        d["scale"] = rnd(rec["sc"])
    if "a0" in rec:
        d["start_angle_deg"] = rnd(math.degrees(rec["a0"]) % 360)
        d["end_angle_deg"] = rnd(math.degrees(rec["a1"]) % 360)
    if "len" in rec:
        d["length"] = rnd(rec["len"])
    if "ar" in rec:
        d["area"] = rnd(rec["ar"])
    return d


def approx_bbox(e: dict) -> list[list[float]] | None:
    """Box from the fields we have (text and blocks: insertion point only)."""
    pts: list[list[float]] = []
    if "start" in e:
        pts += [e["start"], e["end"]]
    if "center" in e and "radius" in e:
        c, r = e["center"], e["radius"]
        pts += [[c[0] - r, c[1] - r], [c[0] + r, c[1] + r]]
    if "vertices" in e:
        pts += e["vertices"]
    for k in ("insertion", "position"):
        if k in e:
            pts.append(e[k])
    if not pts:
        return None
    return [[min(p[0] for p in pts), min(p[1] for p in pts)], [max(p[0] for p in pts), max(p[1] for p in pts)]]


def query(app: Any, doc: Any, flt: str, mode: str = "full", skip: int = 0, limit: int = 0,
          timeout: float = 600.0) -> tuple[int, list[dict[str, Any]]]:
    """Run mcp:query in AutoCAD. Returns (total matched, records converted)."""
    ensure_loaded(app, doc, timeout)
    out = os.path.join(tempfile.gettempdir(), f"acad_mcp_q_{uuid.uuid4().hex}.jsonl")
    try:
        r = C.run_lisp(app, doc, f"(mcp:query {flt} {C.lisp_path(out)} \"{mode}\" {int(skip)} {int(limit)})", timeout)
        if r.get("error") or r.get("cancelled_after_timeout"):
            raise AcadError(t("Fast read failed: {e}", "A leitura rápida falhou: {e}",
                              e=r.get("error") or r.get("note")))
        total = int(r["result"])
        recs: list[dict[str, Any]] = []
        if os.path.exists(out):
            with open(out, "rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", errors="strict") if _is_utf8(raw) else raw.decode("mbcs", "replace")
                    line = line.strip()
                    if line:
                        recs.append(to_entity(json.loads(line)))
        return total, recs
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def _is_utf8(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
