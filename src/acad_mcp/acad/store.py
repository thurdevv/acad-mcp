"""Small objects the server keeps inside the drawing: the definition of each wall and pipe it drew.

They live as XRecords in the same named dictionary as the undo counter (ACAD_MCP), one per object, holding JSON.
Being part of the drawing they are saved with it and rolled back by undo with everything else. The entities drawn
for an object carry its id as extended data (application ACAD_MCP), so they can be told apart later.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pythoncom
import pywintypes
from win32com.client import VARIANT

from .util import MARKER_DICT

PREFIX = "OBJ_"
CHUNK = 240  # characters per string group (old DXF readers stop at 255)


def _dict(doc: Any, create: bool = False) -> Any:
    try:
        return doc.Dictionaries.Item(MARKER_DICT)
    except pywintypes.com_error:
        return doc.Dictionaries.Add(MARKER_DICT) if create else None


def save(doc: Any, obj_id: str, data: dict[str, Any]) -> None:
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
    d = _dict(doc, create=True)
    try:
        xr = d.Item(PREFIX + obj_id)
    except pywintypes.com_error:
        xr = d.AddXRecord(PREFIX + obj_id)
    xr.SetXRecordData(VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, (90, *([1] * len(chunks)))),
                      VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, (len(chunks), *chunks)))


def load(doc: Any, obj_id: str) -> dict[str, Any] | None:
    d = _dict(doc)
    if d is None:
        return None
    try:
        _, values = d.Item(PREFIX + obj_id.strip().upper()).GetXRecordData()
        return json.loads("".join(str(v) for v in values[1:]))
    except (pywintypes.com_error, ValueError, TypeError):
        return None


def ids(doc: Any, letter: str | None = None) -> list[str]:
    """Stored object ids (W1, P3...), in numeric order; only walls ('W') or pipes ('P') when a letter is given."""
    d = _dict(doc)
    out: list[str] = []
    if d is None:
        return out
    for i in range(d.Count):
        try:
            name = str(d.GetName(d.Item(i)))
        except pywintypes.com_error:
            continue
        if name.upper().startswith(PREFIX):
            oid = name[len(PREFIX):].upper()
            if letter is None or oid.startswith(letter.upper()):
                out.append(oid)
    return sorted(out, key=lambda s: (s[:1], int(re.sub(r"\D", "", s) or 0)))


def next_id(doc: Any, letter: str) -> str:
    used = [int(re.sub(r"\D", "", s) or 0) for s in ids(doc, letter)]
    return f"{letter.upper()}{max(used, default=0) + 1}"


def tag(doc: Any, ent: Any, obj_id: str) -> None:
    """Mark an entity as part of an object (best effort: a drawing that refuses extended data still gets drawn)."""
    try:
        try:
            doc.RegisteredApplications.Item(MARKER_DICT)
        except pywintypes.com_error:
            doc.RegisteredApplications.Add(MARKER_DICT)
        ent.SetXData(VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, (1001, 1000)),
                     VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, (MARKER_DICT, obj_id)))
    except pywintypes.com_error:
        pass
