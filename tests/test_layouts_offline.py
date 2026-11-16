"""No AutoCAD needed: scale parsing, paper choice, PDF page size, and the LISP built for dimension styles."""

import pytest

from acad_mcp.acad.errors import AcadError
from acad_mcp.acad.fastpath import lisp_filter
from acad_mcp.tools.layers import dim_style_lisp
from acad_mcp.tools.layouts import parse_ratio, pdf_page_mm, pick_media, ratio_text, safe_file_part

# (canonical, local) names as "DWG To PDF.pc3" reports them on AutoCAD 2027
PDF_MEDIA = [
    ("ISO_A3_(297.00_x_420.00_MM)", "ISO A3 (297.00 x 420.00 MM)"),
    ("ISO_A3_(420.00_x_297.00_MM)", "ISO A3 (420.00 x 297.00 MM)"),
    ("ISO_expand_A3_(420.00_x_297.00_MM)", "ISO expand A3 (420.00 x 297.00 MM)"),
    ("ISO_full_bleed_A3_(297.00_x_420.00_MM)", "ISO full bleed A3 (297.00 x 420.00 MM)"),
    ("ISO_full_bleed_A3_(420.00_x_297.00_MM)", "ISO full bleed A3 (420.00 x 297.00 MM)"),
    ("ISO_full_bleed_A1_(841.00_x_594.00_MM)", "ISO full bleed A1 (841.00 x 594.00 MM)"),
    ("ISO_full_bleed_A1_(594.00_x_841.00_MM)", "ISO full bleed A1 (594.00 x 841.00 MM)"),
    ("ARCH_full_bleed_A_(9.00_x_12.00_Inches)", "ARCH full bleed A (9.00 x 12.00 Inches)"),
]


def test_parse_ratio():
    assert parse_ratio("1:50") == 0.02 and parse_ratio("2:1") == 2.0 and parse_ratio(0.02) == 0.02
    assert parse_ratio("0,5") == 0.5 and parse_ratio("1/100") == 0.01 and parse_ratio(None) is None
    for bad in ("one to fifty", "1:0", 0, -2, "0:5"):
        with pytest.raises(AcadError):
            parse_ratio(bad)


def test_ratio_text():
    assert ratio_text(0.02) == "1:50" and ratio_text(1) == "1:1" and ratio_text(2) == "2:1"
    assert ratio_text(0.2) == "1:5" and ratio_text(0.3) == "0.3"


def test_pick_media():
    assert pick_media(PDF_MEDIA, "A3") == "ISO_full_bleed_A3_(420.00_x_297.00_MM)"            # landscape, borderless
    assert pick_media(PDF_MEDIA, "a3", landscape=False) == "ISO_full_bleed_A3_(297.00_x_420.00_MM)"
    assert pick_media(PDF_MEDIA, "A1", landscape=False) == "ISO_full_bleed_A1_(594.00_x_841.00_MM)"
    assert pick_media(PDF_MEDIA, "ISO A3 (420.00 x 297.00 MM)") == "ISO_A3_(420.00_x_297.00_MM)"  # exact local name
    assert pick_media(PDF_MEDIA, "ISO_expand_A3_(420.00_x_297.00_MM)") == "ISO_expand_A3_(420.00_x_297.00_MM)"
    assert pick_media(PDF_MEDIA, "A") == "ARCH_full_bleed_A_(9.00_x_12.00_Inches)"  # 'A' is not 'A3' nor 'A1'
    assert pick_media(PDF_MEDIA, "A0") is None and pick_media(PDF_MEDIA, "Z9") is None
    printer = [("A4", "A4"), ("A3", "A3"), ("Letter", "Letter")]  # a Windows printer: plain names
    assert pick_media(printer, "A3") == "A3" and pick_media(printer, "a4") == "A4"


def test_pdf_page_mm(tmp_path):
    p = tmp_path / "a3.pdf"
    p.write_bytes(b"%PDF-1.7\n1 0 obj\n<</Type/Page/MediaBox [0 0 1191 842]/Parent 2 0 R>>\nendobj\n")
    assert pdf_page_mm(str(p)) == [420.0, 297.0]
    p.write_bytes(b"%PDF-1.7\nnothing here\n")
    assert pdf_page_mm(str(p)) is None and pdf_page_mm(str(tmp_path / "missing.pdf")) is None


def test_safe_file_part():
    assert safe_file_part("A1 / Planta: térreo") == "A1 _ Planta_ térreo"
    assert safe_file_part('a<b>c"d|e?f*g') == "a_b_c_d_e_f_g" and safe_file_part(" . ") == "layout"


def test_dim_style_lisp():
    code = dim_style_lisp("MCP D", {"text_height": 3.5, "overall_scale": 50, "decimals": 1, "text_style": 'A"B'})
    assert '(tblobjname "DIMSTYLE" "MCP D")' in code and "(cons 140 3.5)" in code and "(cons 40 50.0)" in code
    assert "(cons 271 1)" in code and '(cons 340 (tblobjname "STYLE" "A\\"B"))' in code and "entmod" in code
    assert "(cons 140 0.18)" in dim_style_lisp("X", {"text_height": 0.18})
    assert "(cons 144 0.001)" in dim_style_lisp("X", {"linear_factor": 0.001})  # never 1e-03: LISP would not read it


def test_layout_filter_replaces_space():
    assert lisp_filter(space="model") == '(list (cons 410 "Model"))'
    assert lisp_filter(space="model", layout="A1-0*") == '(list (cons 410 "A1-0*"))'
    assert lisp_filter(types=["text"], space="all", layout="Sheet 1") == '(list (cons 0 "TEXT") (cons 410 "Sheet 1"))'
