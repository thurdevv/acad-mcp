"""Receiving and validating files, without AutoCAD."""

import base64
import io

import ezdxf
import pytest
from PIL import Image

from acad_mcp.acad.errors import AcadError
from acad_mcp.tools import files

MIN_DXF = "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n30\n0\n11\n200\n21\n100\n31\n0\n0\nENDSEC\n0\nEOF\n"


@pytest.fixture(autouse=True)
def tmp_received(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "RECEIVED_DIR", tmp_path / "recebidos")
    return tmp_path


def test_min_dxf_text_is_saved_and_valid():
    p = files.receive("teste.dxf", MIN_DXF, None, None)
    assert p.read_text() == MIN_DXF
    info = files.validate(p)
    assert info["format"] == "dxf" and info["entities_in_file"] == 1


def test_never_overwrites():
    a = files.receive("x.dxf", MIN_DXF, None, None)
    b = files.receive("x.dxf", MIN_DXF, None, None)
    assert a != b and a.exists() and b.exists() and b.name == "x_1.dxf"


def test_unsafe_name_is_sanitized():
    p = files.receive("..\\..\\Windows\\evil.dxf", MIN_DXF, None, None)
    assert p.parent == files.RECEIVED_DIR and p.name == "evil.dxf"


def test_invalid_dxf_rejected_before_autocad():
    p = files.receive("ruim.dxf", "isto nao e um dxf", None, None)
    with pytest.raises(AcadError):
        files.validate(p)


def test_full_dxf_from_ezdxf(tmp_path):
    d = ezdxf.new("R2010")
    d.modelspace().add_circle((0, 0), 5)
    buf = io.StringIO()
    d.write(buf)
    p = files.receive("c.dxf", buf.getvalue(), None, None)
    assert files.validate(p)["entities_in_file"] == 1


def test_png_base64():
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "PNG")
    p = files.receive("a.png", None, base64.b64encode(buf.getvalue()).decode(), None)
    assert files.validate(p) == {"format": "image", "pixels": [40, 20]}


def test_fake_dwg_rejected():
    p = files.receive("x.dwg", None, base64.b64encode(b"nao sou dwg").decode(), None)
    with pytest.raises(AcadError):
        files.validate(p)


def test_exactly_one_source():
    with pytest.raises(AcadError):
        files.receive("a.dxf", MIN_DXF, "QUJD", None)
    with pytest.raises(AcadError):
        files.receive("a.dxf", None, None, None)
    with pytest.raises(AcadError):
        files.receive(None, MIN_DXF, None, None)
    with pytest.raises(AcadError):
        files.receive(None, None, None, "C:/nao/existe.dxf")


def test_unsupported_extension():
    p = files.receive("x.exe", "MZ", None, None)
    with pytest.raises(AcadError):
        files.validate(p)
