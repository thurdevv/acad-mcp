"""Conversions and access to AutoCAD objects through COM."""

from __future__ import annotations

import math
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

import pythoncom
import pywintypes
from win32com.client import VARIANT

from ..i18n import t
from .errors import AcadError

Point = Sequence[float]

# ------------------------------------------------------------------ geometria


def P(p: Point) -> VARIANT:
    """Ponto [x, y] ou [x, y, z] -> VARIANT de 3 doubles."""
    if len(p) not in (2, 3):
        raise AcadError(t("Invalid point {p}: use [x, y] or [x, y, z].",
                          "Ponto inválido {p}: use [x, y] ou [x, y, z].", p=list(p)))
    x, y = float(p[0]), float(p[1])
    z = float(p[2]) if len(p) == 3 else 0.0
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (x, y, z))


def flat2d(points: Sequence[Point]) -> VARIANT:
    vals: list[float] = []
    for p in points:
        vals += [float(p[0]), float(p[1])]
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(vals))


def flat3d(points: Sequence[Point]) -> VARIANT:
    vals: list[float] = []
    for p in points:
        vals += [float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0]
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(vals))


def objs(items: Sequence[Any]) -> VARIANT:
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(items))


def rnd(v: float, nd: int = 6) -> float:
    r = round(float(v), nd)
    return 0.0 if r == 0 else r


def pt_out(p: Iterable[float], dims: int = 3) -> list[float]:
    vals = [rnd(v) for v in p]
    if dims == 2 or (len(vals) == 3 and vals[2] == 0.0):
        return vals[:2]
    return vals


def rad(deg: float) -> float:
    return math.radians(float(deg))


def deg(r: float) -> float:
    return rnd(math.degrees(float(r)) % 360.0, 6)


# ------------------------------------------------------------------ documento


def active_doc(app: Any) -> Any:
    if app.Documents.Count == 0:
        raise AcadError(t(
            "No drawing is open in AutoCAD. Use new_drawing or open_drawing first.",
            "Nenhum desenho aberto no AutoCAD. Use new_drawing ou open_drawing primeiro.",
        ))
    return app.ActiveDocument


def target_space(doc: Any, space: str = "model") -> Any:
    s = (space or "model").lower()
    if s in ("model", "modelspace", "model_space"):
        return doc.ModelSpace
    if s in ("paper", "paperspace", "paper_space", "layout"):
        return doc.PaperSpace
    raise AcadError(t("space must be 'model' or 'paper'.", "space deve ser 'model' ou 'paper'."))


def by_handle(doc: Any, handle: str) -> Any:
    try:
        return doc.HandleToObject(str(handle).strip().upper())
    except pywintypes.com_error:
        raise AcadError(t(
            "No entity with handle '{h}' in this drawing (erased, or from another drawing?).",
            "Entidade com handle '{h}' não existe neste desenho (foi apagada ou é de outro desenho?).",
            h=handle,
        )) from None


def many_by_handle(doc: Any, handles: Sequence[str]) -> list[Any]:
    if not handles:
        raise AcadError(t("Give at least one handle.", "Informe pelo menos um handle."))
    return [by_handle(doc, h) for h in handles]


# ------------------------------------------------------------------ undo steps
#
# Every write made through the server is ONE undo group and bumps a counter stored in the drawing (an XRecord
# in the named dictionary ACAD_MCP), INSIDE that group. Measured on AutoCAD 2027: undoing the group rolls the
# counter back with it, and internal records left by read-only queries do not touch it. So `undo` can send U
# until the counter drops, without reading AutoCAD's (localized) prompts.

UNDO_MARKER = True  # set by server.build() from Config (ACAD_MCP_UNDO_MARKER=0 turns it off)
MARKER_DICT = "ACAD_MCP"
MARKER_KEY = "undo"


def open_token(doc: Any) -> str:
    """Identifies THIS opening of the document: the undo stack does not survive closing, the saved counter does."""
    try:
        return f"{doc.Application.HWND}:{doc.HWND}:{doc.ModelSpace.ObjectID}"
    except Exception:  # noqa: BLE001
        return "?"


def read_undo_marker(doc: Any) -> tuple[int, str]:
    """(counter, token) stored in the drawing; (0, '') when there is none."""
    try:
        _, values = doc.Dictionaries.Item(MARKER_DICT).Item(MARKER_KEY).GetXRecordData()
        return int(values[0]), str(values[1]) if len(values) > 1 else ""
    except Exception:  # noqa: BLE001
        return 0, ""


def pending_undo_steps(doc: Any) -> int:
    """How many server write steps are still in this document's undo stack."""
    count, token = read_undo_marker(doc)
    return count if count > 0 and token == open_token(doc) else 0


def bump_undo_counter(doc: Any) -> None:
    """Call inside an open undo group. Never raises: a marker problem must not break the tool."""
    if not UNDO_MARKER:
        return
    try:
        token = open_token(doc)
        count, stored = read_undo_marker(doc)
        count = count + 1 if stored == token else 1
        dicts = doc.Dictionaries
        try:
            d = dicts.Item(MARKER_DICT)
        except pywintypes.com_error:
            d = dicts.Add(MARKER_DICT)
        try:
            xr = d.Item(MARKER_KEY)
        except pywintypes.com_error:
            xr = d.AddXRecord(MARKER_KEY)
        xr.SetXRecordData(VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, (90, 1)),
                          VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, (int(count), token)))
    except Exception:  # noqa: BLE001
        pass


def remove_undo_marker(doc: Any) -> bool:
    """Delete the counter (and the dictionary if it becomes empty). Returns True if something was removed."""
    try:
        d = doc.Dictionaries.Item(MARKER_DICT)
    except pywintypes.com_error:
        return False
    removed = False
    try:
        d.Item(MARKER_KEY).Delete()
        removed = True
    except pywintypes.com_error:
        pass
    try:
        if d.Count == 0:
            d.Delete()
            removed = True
    except pywintypes.com_error:
        pass
    return removed


@contextmanager
def without_undo_marker() -> Iterator[None]:
    """For work on other people's files (batch): group the changes but leave no counter in the drawing."""
    global UNDO_MARKER
    old, UNDO_MARKER = UNDO_MARKER, False
    try:
        yield
    finally:
        UNDO_MARKER = old


class UndoStep:
    """Yielded by undo_group; set .count = False when the group turned out to change nothing."""

    count = True


@contextmanager
def undo_group(doc: Any) -> Iterator[UndoStep]:
    """Everything inside becomes a single undo step (one Ctrl+Z), counted when it completes."""
    step = UndoStep()
    doc.StartUndoMark()
    try:
        yield step
        if step.count:
            bump_undo_counter(doc)
    finally:
        try:
            doc.EndUndoMark()
        except pywintypes.com_error:
            pass


# ------------------------------------------------------------------ cores e propriedades

ACI_NAMES = {
    "bylayer": 256, "porlayer": 256, "byblock": 0, "porbloco": 0,
    "red": 1, "vermelho": 1, "yellow": 2, "amarelo": 2, "green": 3, "verde": 3,
    "cyan": 4, "ciano": 4, "blue": 5, "azul": 5, "magenta": 6, "white": 7, "branco": 7,
    "black": 7, "preto": 7, "gray": 8, "cinza": 8, "grey": 8, "lightgray": 9, "cinzaclaro": 9,
}


def parse_color(color: int | str) -> tuple[str, Any]:
    """-> ('aci', int) ou ('rgb', (r,g,b))."""
    if isinstance(color, bool):
        raise AcadError(t("Invalid color.", "Cor inválida."))
    if isinstance(color, int):
        if not 0 <= color <= 256:
            raise AcadError(t("ACI color must be 0 to 256 (256 = ByLayer, 0 = ByBlock).",
                              "Cor ACI deve ser de 0 a 256 (256 = ByLayer, 0 = ByBlock)."))
        return ("aci", color)
    s = str(color).strip().lower().replace(" ", "").replace("_", "")
    if s.isdigit():
        return parse_color(int(s))
    if s in ACI_NAMES:
        return ("aci", ACI_NAMES[s])
    if s.startswith("#") and len(s) == 7:
        return ("rgb", tuple(int(s[i : i + 2], 16) for i in (1, 3, 5)))
    parts = s.replace(";", ",").split(",")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        rgb = tuple(int(p) for p in parts)
        if all(0 <= v <= 255 for v in rgb):
            return ("rgb", rgb)
    raise AcadError(t(
        "Unknown color '{c}'. Use an ACI number (1=red, 7=white, 256=ByLayer), a name (red, bylayer...), "
        "'#RRGGBB' or 'R,G,B'.",
        "Cor '{c}' não reconhecida. Use número ACI (1=vermelho, 7=branco, 256=ByLayer), "
        "nome (red, bylayer...), '#RRGGBB' ou 'R,G,B'.",
        c=color,
    ))


def set_color(app: Any, obj: Any, color: int | str) -> None:
    kind, val = parse_color(color)
    tc = obj.TrueColor
    if kind == "aci":
        tc.ColorIndex = val
    else:
        tc.SetRGB(*val)
    obj.TrueColor = tc


def color_out(obj: Any) -> Any:
    try:
        tc = obj.TrueColor
        method = tc.ColorMethod
        if method == 192:  # acColorMethodByLayer
            return "ByLayer"
        if method == 193:  # ByBlock
            return "ByBlock"
        if method == 194:  # ByRGB
            return f"{tc.Red},{tc.Green},{tc.Blue}"
        return tc.ColorIndex
    except Exception:  # noqa: BLE001
        return None


def ensure_layer(doc: Any, name: str) -> bool:
    """Create the layer if missing. Returns True if it was created."""
    try:
        doc.Layers.Item(name)
        return False
    except pywintypes.com_error:
        doc.Layers.Add(name)
        return True


def ensure_linetype(doc: Any, name: str) -> None:
    if name.lower() in ("bylayer", "byblock", "continuous"):
        return
    try:
        doc.Linetypes.Item(name)
        return
    except pywintypes.com_error:
        pass
    last: Exception | None = None
    for f in ("acadiso.lin", "acad.lin"):
        try:
            doc.Linetypes.Load(name, f)
            return
        except pywintypes.com_error as exc:
            last = exc
    raise AcadError(t("Linetype '{n}' not found in acadiso.lin/acad.lin. ({e})",
                      "Tipo de linha '{n}' não encontrado em acadiso.lin/acad.lin. ({e})", n=name, e=last))


LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]


def parse_lineweight(lw: int | float | str) -> int:
    """Accepts 'bylayer', 'byblock', 'default', mm (0.35) or hundredths (35)."""
    if isinstance(lw, str):
        s = lw.strip().lower()
        special = {"bylayer": -1, "byblock": -2, "default": -3, "padrao": -3, "padrão": -3}
        if s in special:
            return special[s]
        lw = float(s.replace("mm", "").replace(",", "."))
    v = float(lw)
    if v < 0:
        return int(v)
    hundredths = round(v * 100) if v < 3 else round(v)
    if hundredths not in LINEWEIGHTS:
        nearest = min(LINEWEIGHTS, key=lambda x: abs(x - hundredths))
        valid = ", ".join(f"{x / 100:.2f}" for x in LINEWEIGHTS)
        raise AcadError(t(
            "Lineweight {lw} is not an AutoCAD standard. Nearest: {n:.2f} mm. Valid (mm): {v}.",
            "Espessura {lw} não é padrão do AutoCAD. Mais próxima: {n:.2f} mm. Válidas (mm): {v}.",
            lw=lw, n=nearest / 100, v=valid,
        ))
    return hundredths


def apply_props(
    app: Any,
    doc: Any,
    ent: Any,
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
    lineweight: int | float | str | None = None,
    linetype_scale: float | None = None,
) -> list[str]:
    """Aplica propriedades comuns. Retorna avisos (ex.: layer criado)."""
    notes: list[str] = []
    if layer:
        if ensure_layer(doc, layer):
            notes.append(t("layer '{l}' created", "layer '{l}' criado", l=layer))
        ent.Layer = layer
    if color is not None:
        set_color(app, ent, color)
    if linetype:
        ensure_linetype(doc, linetype)
        ent.Linetype = linetype
    if lineweight is not None:
        ent.Lineweight = parse_lineweight(lineweight)
    if linetype_scale is not None:
        ent.LinetypeScale = float(linetype_scale)
    return notes


# ------------------------------------------------------------------ serialization


def _g(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr)
    except Exception:  # noqa: BLE001
        return default


def short_type(object_name: str) -> str:
    return object_name[4:] if object_name.startswith("AcDb") else object_name


def bbox(ent: Any) -> list[list[float]] | None:
    try:
        mn, mx = ent.GetBoundingBox()
        return [pt_out(mn), pt_out(mx)]
    except Exception:  # noqa: BLE001
        return None


def _plain(v: Any) -> Any:
    if isinstance(v, float):
        return rnd(v)
    if isinstance(v, (tuple, list)):
        return [_plain(x) for x in v]
    return v


def dynamic_properties(br: Any) -> list[dict[str, Any]]:
    """Properties of a dynamic block reference (the hidden read-only 'Origin' entries are left out)."""
    out: list[dict[str, Any]] = []
    for p in br.GetDynamicBlockProperties():
        ro = bool(_g(p, "ReadOnly", False))
        if ro and not _g(p, "Show", True):
            continue
        item: dict[str, Any] = {"name": p.PropertyName, "value": _plain(p.Value)}
        try:
            allowed = [_plain(a) for a in p.AllowedValues]
        except Exception:  # noqa: BLE001
            allowed = []
        if allowed:
            item["allowed_values"] = allowed
        if ro:
            item["read_only"] = True
        desc = _g(p, "Description", "")
        if desc:
            item["description"] = desc
        out.append(item)
    return out


def set_dynamic_properties(br: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Set dynamic block properties by name (case-insensitive). Returns the values read back.

    Measured on AutoCAD 2027: a float property refuses a Python int, an integer (flip) property needs a 16-bit
    integer, a text property silently ignores a value that is not in its list, and a number that is not in the
    list snaps to the nearest allowed one. So values are converted to the property's own type and checked here.
    """
    if not _g(br, "IsDynamicBlock", False):
        raise AcadError(t("That entity is not a dynamic block.", "Essa entidade não é um bloco dinâmico."))
    props = {}
    for p in br.GetDynamicBlockProperties():
        if not (_g(p, "ReadOnly", False)):
            props.setdefault(p.PropertyName.upper(), p)
    unknown = sorted(k for k in values if k.upper() not in props)
    if unknown:
        raise AcadError(t("Unknown dynamic properties: {u}. This block has: {a}",
                          "Propriedades dinâmicas inexistentes: {u}. Este bloco tem: {a}",
                          u=unknown, a=sorted(p.PropertyName for p in props.values())))
    out: dict[str, Any] = {}
    for key, new in values.items():
        p = props[key.upper()]
        cur = p.Value
        try:
            allowed = list(p.AllowedValues)
        except Exception:  # noqa: BLE001
            allowed = []
        try:
            if isinstance(cur, str):
                val: Any = str(new)
                match = next((a for a in allowed if str(a).upper() == val.upper()), None)
                if allowed and match is None:
                    raise AcadError(t("'{n}' must be one of {a}.", "'{n}' precisa ser um destes: {a}.",
                                      n=p.PropertyName, a=allowed))
                p.Value = match if match is not None else val
            elif isinstance(cur, bool) or isinstance(cur, int):
                iv = int(float(new))
                if allowed and iv not in [int(a) for a in allowed]:
                    raise AcadError(t("'{n}' must be one of {a}.", "'{n}' precisa ser um destes: {a}.",
                                      n=p.PropertyName, a=allowed))
                try:
                    p.Value = VARIANT(pythoncom.VT_I2, iv)
                except pywintypes.com_error:
                    p.Value = VARIANT(pythoncom.VT_I4, iv)
            elif isinstance(cur, float):
                p.Value = float(new)
            else:
                raise AcadError(t("'{n}' is a point property and cannot be set here.",
                                  "'{n}' é uma propriedade de ponto e não pode ser alterada aqui.", n=p.PropertyName))
        except (TypeError, ValueError):
            raise AcadError(t("'{n}' needs a value like {c!r}, got {v!r}.",
                              "'{n}' precisa de um valor como {c!r}; veio {v!r}.",
                              n=p.PropertyName, c=cur, v=new)) from None
        except pywintypes.com_error as exc:
            raise AcadError(t("AutoCAD refused {v!r} for '{n}' ({e}).", "O AutoCAD recusou {v!r} em '{n}' ({e}).",
                              v=new, n=p.PropertyName, e=exc.args[1])) from None
        out[p.PropertyName] = _plain(p.Value)
    return out


def entity_info(ent: Any, detail: bool = False, with_bbox: bool = False) -> dict[str, Any]:
    on = _g(ent, "ObjectName", "?")
    d: dict[str, Any] = {
        "handle": _g(ent, "Handle"),
        "type": short_type(on),
        "layer": _g(ent, "Layer"),
        "color": color_out(ent),
    }
    if detail:
        d["linetype"] = _g(ent, "Linetype")
        d["lineweight"] = _g(ent, "Lineweight")
    t = d["type"]
    try:
        if t == "Line":
            d.update(start=pt_out(ent.StartPoint), end=pt_out(ent.EndPoint),
                     length=rnd(ent.Length), angle_deg=deg(ent.Angle))
        elif t == "Circle":
            d.update(center=pt_out(ent.Center), radius=rnd(ent.Radius), area=rnd(ent.Area),
                     circumference=rnd(ent.Circumference))
        elif t == "Arc":
            d.update(center=pt_out(ent.Center), radius=rnd(ent.Radius),
                     start_angle_deg=deg(ent.StartAngle), end_angle_deg=deg(ent.EndAngle),
                     length=rnd(ent.ArcLength), start=pt_out(ent.StartPoint), end=pt_out(ent.EndPoint))
        elif t == "Polyline":  # LWPOLYLINE
            c = list(ent.Coordinates)
            verts = [[rnd(c[i]), rnd(c[i + 1])] for i in range(0, len(c), 2)]
            d.update(closed=bool(ent.Closed), length=rnd(ent.Length), vertex_count=len(verts))
            if bool(ent.Closed):
                d["area"] = rnd(ent.Area)
            if detail or len(verts) <= 50:
                d["vertices"] = verts
            if detail:
                bul = [rnd(ent.GetBulge(i)) for i in range(len(verts))]
                if any(bul):
                    d["bulges"] = bul
                d["elevation"] = rnd(ent.Elevation)
        elif t in ("2dPolyline", "3dPolyline"):
            c = list(ent.Coordinates)
            verts = [pt_out(c[i : i + 3]) for i in range(0, len(c), 3)]
            d.update(closed=bool(_g(ent, "Closed", False)), length=rnd(ent.Length), vertex_count=len(verts))
            if detail or len(verts) <= 50:
                d["vertices"] = verts
        elif t == "Text":
            d.update(text=ent.TextString, insertion=pt_out(ent.InsertionPoint), height=rnd(ent.Height),
                     rotation_deg=deg(ent.Rotation), style=_g(ent, "StyleName"))
        elif t == "MText":
            d.update(text=ent.TextString, insertion=pt_out(ent.InsertionPoint), height=rnd(ent.Height),
                     width=rnd(ent.Width), rotation_deg=deg(ent.Rotation), style=_g(ent, "StyleName"))
        elif t == "BlockReference":
            d.update(name=_g(ent, "EffectiveName") or ent.Name, insertion=pt_out(ent.InsertionPoint),
                     rotation_deg=deg(ent.Rotation),
                     scale=[rnd(ent.XScaleFactor), rnd(ent.YScaleFactor), rnd(ent.ZScaleFactor)])
            if _g(ent, "IsDynamicBlock", False):
                d["dynamic"] = True
                if ent.Name != d["name"]:
                    d["anonymous_name"] = ent.Name
                if detail:
                    d["dynamic_properties"] = dynamic_properties(ent)
            if _g(ent, "HasAttributes", False):
                d["attributes"] = {a.TagString: a.TextString for a in ent.GetAttributes()}
        elif "Dimension" in on:
            d.update(measurement=rnd(ent.Measurement), text_override=_g(ent, "TextOverride"),
                     style=_g(ent, "StyleName"))
        elif t == "Hatch":
            d.update(pattern=ent.PatternName, area=rnd(_g(ent, "Area", 0.0)),
                     scale=rnd(_g(ent, "PatternScale", 1.0)), angle_deg=deg(_g(ent, "PatternAngle", 0.0)))
        elif t == "Point":
            d.update(position=pt_out(ent.Coordinates))
        elif t == "Ellipse":
            d.update(center=pt_out(ent.Center), major_radius=rnd(ent.MajorRadius),
                     minor_radius=rnd(ent.MinorRadius), area=rnd(_g(ent, "Area", 0.0)))
        elif t == "Table":
            d.update(rows=ent.Rows, columns=ent.Columns, insertion=pt_out(ent.InsertionPoint))
            if detail:
                d["cells"] = [[ent.GetText(r, c) for c in range(ent.Columns)] for r in range(ent.Rows)]
        elif t == "MLeader":
            d.update(text=_g(ent, "TextString"))
        elif t in ("Region", "Spline"):
            d.update(area=rnd(_g(ent, "Area", 0.0)))
    except Exception as exc:  # noqa: BLE001
        d["read_warning"] = str(exc)[:120]
    if with_bbox or detail:
        d["bbox"] = bbox(ent)
    if detail:
        tag = object_tag(ent)
        if tag:
            d["object"] = tag  # the wall / pipe (W1, P2...) this entity belongs to: see get_wall
    return d


def object_tag(ent: Any) -> str | None:
    """Id of the server object (wall, pipe) an entity was drawn for, from its extended data."""
    try:
        _, values = ent.GetXData(MARKER_DICT)
        return str(values[1]) if values and len(values) > 1 else None
    except Exception:  # noqa: BLE001
        return None


def curve_length(ent: Any) -> float | None:
    t = short_type(_g(ent, "ObjectName", ""))
    if t in ("Line", "Polyline", "2dPolyline", "3dPolyline"):
        return float(_g(ent, "Length", 0.0) or 0.0)
    if t == "Arc":
        return float(_g(ent, "ArcLength", 0.0) or 0.0)
    if t == "Circle":
        return float(_g(ent, "Circumference", 0.0) or 0.0)
    return None


def closed_area(ent: Any) -> float | None:
    t = short_type(_g(ent, "ObjectName", ""))
    if t == "Polyline":
        return float(ent.Area) if bool(_g(ent, "Closed", False)) else None
    if t in ("Circle", "Hatch", "Region", "Ellipse"):
        return float(_g(ent, "Area", 0.0) or 0.0)
    return None


# ------------------------------------------------------------------ selection by filter

TYPE_MAP = {
    "line": "LINE", "linha": "LINE", "circle": "CIRCLE", "circulo": "CIRCLE", "círculo": "CIRCLE",
    "arc": "ARC", "arco": "ARC", "polyline": "LWPOLYLINE,POLYLINE", "polilinha": "LWPOLYLINE,POLYLINE",
    "lwpolyline": "LWPOLYLINE", "text": "TEXT", "texto": "TEXT", "mtext": "MTEXT",
    "anytext": "TEXT,MTEXT", "block": "INSERT", "bloco": "INSERT", "insert": "INSERT",
    "blockreference": "INSERT", "dimension": "DIMENSION", "cota": "DIMENSION",
    "hatch": "HATCH", "hachura": "HATCH", "point": "POINT", "ponto": "POINT", "spline": "SPLINE",
    "ellipse": "ELLIPSE", "elipse": "ELLIPSE", "leader": "LEADER,MULTILEADER",
    "mleader": "MULTILEADER", "table": "ACAD_TABLE", "tabela": "ACAD_TABLE", "region": "REGION",
    "solid": "SOLID", "xline": "XLINE", "ray": "RAY", "image": "IMAGE", "viewport": "VIEWPORT",
}


def dxf_types(types: Sequence[str] | None) -> str | None:
    if not types:
        return None
    out = []
    for t in types:
        k = t.strip().lower().replace(" ", "").replace("_", "")
        out.append(TYPE_MAP.get(k, t.strip().upper()))
    return ",".join(out)


def select(
    doc: Any,
    types: Sequence[str] | None = None,
    layers: Sequence[str] | None = None,
    space: str = "model",
    text_contains: str | None = None,
    color: int | None = None,
    layout: str | None = None,
) -> list[Any]:
    """Select entities with AutoCAD's native filter (accepts * ? wildcards).

    space: 'model', 'paper' (all layouts) or 'all'. layout: a layout (sheet) name, replaces space.
    """
    codes: list[int] = []
    data: list[Any] = []
    dt = dxf_types(types)
    if dt:
        codes.append(0)
        data.append(dt)
    if layers:
        codes.append(8)
        data.append(",".join(layers))
    s = (space or "model").lower()
    if layout:
        codes.append(410)
        data.append(layout)
    elif s.startswith("model"):
        codes.append(410)
        data.append("Model")
    elif s.startswith("paper") or s == "layout":
        codes += [-4, 410, -4]
        data += ["<NOT", "Model", "NOT>"]
    elif s != "all":
        raise AcadError(t("space must be 'model', 'paper' or 'all'.", "space deve ser 'model', 'paper' ou 'all'."))
    if text_contains:
        codes.append(1)
        data.append(f"*{text_contains}*")
    if color is not None:
        codes.append(62)
        data.append(int(color))

    name = "MCP_" + uuid.uuid4().hex[:10]
    ss = doc.SelectionSets.Add(name)
    try:
        if codes:
            ft = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, tuple(codes))
            fd = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, tuple(data))
            ss.Select(5, None, None, ft, fd)  # acSelectionSetAll
        else:
            ss.Select(5)
        return [ss.Item(i) for i in range(ss.Count)]
    finally:
        try:
            ss.Delete()
        except pywintypes.com_error:
            pass


def filter_block_name(ents: Iterable[Any], block_name: str) -> list[Any]:
    """Filter block references by effective name (includes dynamic blocks, whose internal
    name is anonymous *U...). Accepts wildcards and commas: 'DOOR*,WINDOW'."""
    import fnmatch

    pats = [p.strip().upper() for p in block_name.split(",") if p.strip()]
    out = []
    for e in ents:
        if short_type(_g(e, "ObjectName", "")) != "BlockReference":
            continue
        nm = (_g(e, "EffectiveName") or _g(e, "Name") or "").upper()
        if any(fnmatch.fnmatchcase(nm, p) for p in pats):
            out.append(e)
    return out


def inside_box(ent: Any, box: Sequence[Point], mode: str = "crossing") -> bool:
    b = bbox(ent)
    if b is None:
        return False
    (x1, y1), (x2, y2) = (box[0][0], box[0][1]), (box[1][0], box[1][1])
    lo = (min(x1, x2), min(y1, y2))
    hi = (max(x1, x2), max(y1, y2))
    emin, emax = b[0], b[1]
    if mode == "inside":
        return lo[0] <= emin[0] and lo[1] <= emin[1] and emax[0] <= hi[0] and emax[1] <= hi[1]
    return not (emax[0] < lo[0] or emin[0] > hi[0] or emax[1] < lo[1] or emin[1] > hi[1])
