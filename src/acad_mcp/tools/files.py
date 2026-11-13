"""Receive files (DXF, DWG, PDF, images) and bring them into AutoCAD.

The AI model cannot touch this PC's disk: files it creates in its own code sandbox stay in the cloud.
This tool takes the file CONTENT as a parameter (text or base64), writes it locally and opens/inserts
it in AutoCAD.

Measured on AutoCAD 2027:
- InsertBlock does NOT accept .dxf ("Invalid drawing file header"): the DXF is opened, saved as .dwg
  (native AC1032) and then inserted.
- A minimal R12 DXF (ENTITIES section only) opens fine.
- AddRaster works with PNG; the size argument is the image width in drawing units.
- -PDFIMPORT works from the command line with FILEDIA=0 (no dialog): vector PDFs come back as
  polylines on PDF_<layer> layers with the original measurements (4000x3000 came back 3999.6x2999.1).
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from ..acad import commands as C
from ..acad.errors import AcadError
from ..acad.util import P, active_doc, apply_props, bbox, entity_info, rad, undo_group
from ..i18n import t
from .common import ADD, RO, Ctx, Pt

RECEIVED_DIR = Path(os.environ.get("ACAD_MCP_RECEIVED_DIR", Path.home() / "Documents" / "AutoCAD MCP" / "received"))
MAX_BYTES = 25 * 1024 * 1024
CAD_EXT = {".dxf", ".dwg"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

Action = Literal["auto", "insert_exploded", "insert_block", "open", "attach_image", "import_pdf", "save_only"]


def _safe_name(name: str) -> str:
    base = os.path.basename(name.strip().replace("\\", "/")) or "file"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    return base[:120]


def _unique(folder: Path, name: str) -> Path:
    p = folder / name
    stem, ext = p.stem, p.suffix
    i = 1
    while p.exists():
        p = folder / f"{stem}_{i}{ext}"
        i += 1
    return p


def receive(filename: str | None, content_text: str | None, content_base64: str | None,
            source_path: str | None) -> Path:
    """Write the received content (or locate the file on this PC). Never overwrites."""
    given = sum(x is not None for x in (content_text, content_base64, source_path))
    if given != 1:
        raise AcadError(t("Give exactly one of: content_text, content_base64 or source_path.",
                          "Informe exatamente um: content_text, content_base64 ou source_path."))
    if source_path:
        p = Path(source_path)
        if not p.is_file():
            raise AcadError(t("File not found on this PC: {p}", "Arquivo não encontrado neste PC: {p}", p=source_path))
        return p
    if not filename or not Path(filename).suffix:
        raise AcadError(t("Give filename with an extension (e.g. 'plan.dxf', 'logo.png').",
                          "Informe filename com extensão (ex.: 'planta.dxf', 'logo.png')."))
    RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
    dest = _unique(RECEIVED_DIR, _safe_name(filename))
    if content_text is not None:
        data = content_text.replace("\r\n", "\n").encode("utf-8")
    else:
        try:
            data = base64.b64decode(content_base64 or "", validate=False)
        except (binascii.Error, ValueError):
            raise AcadError(t("Invalid content_base64.", "content_base64 inválido.")) from None
    if not data:
        raise AcadError(t("The file content is empty.", "O conteúdo do arquivo veio vazio."))
    if len(data) > MAX_BYTES:
        raise AcadError(t("File too large ({k} KB). Limit: {m} MB.", "Arquivo grande demais ({k} KB). Limite: {m} MB.",
                          k=len(data) // 1024, m=MAX_BYTES // 1024 // 1024))
    dest.write_bytes(data)
    return dest


def validate(path: Path) -> dict[str, Any]:
    """Check the file BEFORE handing it to AutoCAD (a broken DXF can freeze AutoCAD in a dialog)."""
    ext = path.suffix.lower()
    if ext == ".dxf":
        from ezdxf import recover
        from ezdxf.lldxf.const import DXFStructureError

        try:
            doc, auditor = recover.readfile(str(path))
        except (OSError, DXFStructureError) as exc:
            raise AcadError(t("Invalid DXF ({e}). Check the format: code/value pairs, ending with '0\\nEOF'.",
                              "DXF inválido ({e}). Confira o formato: pares código/valor, termina com '0\\nEOF'.",
                              e=exc)) from None
        if auditor.has_errors:
            msgs = "; ".join(str(e.message) for e in auditor.errors[:5])
            raise AcadError(t("DXF has errors that prevent reading it: {m}", "DXF com erros que impedem a leitura: {m}",
                              m=msgs))
        return {"format": "dxf", "dxf_version": doc.dxfversion, "entities_in_file": len(doc.modelspace()),
                "fixes_applied_by_check": len(auditor.fixes)}
    if ext == ".dwg":
        with open(path, "rb") as fh:
            head = fh.read(6)
        if not head.startswith(b"AC10"):
            raise AcadError(t("Not a valid DWG (header is not 'AC10..').",
                              "Não parece um DWG válido (cabeçalho diferente de 'AC10..')."))
        return {"format": "dwg", "dwg_version": head.decode("ascii", "replace")}
    if ext == ".pdf":
        with open(path, "rb") as fh:
            head = fh.read(5)
        if head != b"%PDF-":
            raise AcadError(t("Not a valid PDF.", "Não parece um PDF válido."))
        return {"format": "pdf"}
    if ext in IMG_EXT:
        from PIL import Image

        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                return {"format": "image", "pixels": list(im.size)}
        except Exception as exc:  # noqa: BLE001
            raise AcadError(t("Invalid image: {e}", "Imagem inválida: {e}", e=exc)) from None
    raise AcadError(t("Extension '{x}' not supported. Use .dxf, .dwg, .pdf or an image (.png, .jpg, .bmp, .tif).",
                      "Extensão '{x}' não suportada. Use .dxf, .dwg, .pdf ou imagem (.png, .jpg, .bmp, .tif).", x=ext))


def _find_doc_by_path(app: Any, path: Path) -> Any | None:
    target = str(path).lower()
    for i in range(app.Documents.Count):
        d = app.Documents.Item(i)
        if (d.FullName or "").lower() == target:
            return d
    return None


def dxf_to_dwg(app: Any, dxf: Path) -> Path:
    """Open the DXF, save it as DWG next to it and close it. Returns the DWG path."""
    if _find_doc_by_path(app, dxf) is not None:
        raise AcadError(t("{n} is already open in AutoCAD; close it before inserting.",
                          "{n} já está aberto no AutoCAD; feche-o antes de inserir.", n=dxf.name))
    dwg = _unique(dxf.parent, dxf.stem + "_converted.dwg")
    src = app.Documents.Open(str(dxf))
    try:
        src.SaveAs(str(dwg))
    finally:
        src.Close(False)
    if not dwg.is_file():
        raise AcadError(t("DXF to DWG conversion failed.", "A conversão de DXF para DWG falhou."))
    return dwg


def _extents(ents: list[Any]) -> list[list[float]] | None:
    boxes = [b for b in (bbox(e) for e in ents) if b]
    if not boxes:
        return None
    return [[min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)],
            [max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)]]


def register(ctx: Ctx) -> None:
    @ctx.tool(ADD, title=t("Import file (DXF, DWG, PDF, image)", "Importar arquivo (DXF, DWG, PDF, imagem)"), pt=(
        "Leva um arquivo para o AutoCAD: grava neste PC e abre, insere no desenho ativo, importa a geometria de um "
        "PDF vetorial (medidas reais) ou anexa uma imagem de referência. Use quando já existir um arquivo pronto — o "
        "seu ambiente de código fica na nuvem e NÃO alcança o AutoCAD, então mande o conteúdo por aqui. Para "
        "desenhar algo novo, prefira draw_batch. Arquivos recebidos ficam em Documentos\\AutoCAD MCP\\received "
        "(nunca sobrescreve)."))
    def import_file(
        filename: Annotated[str | None, Field(description=t(
            "Name with extension, e.g. 'plan.dxf', 'part.dwg', 'sheet.pdf', 'sketch.png'. Required with content_*.",
            "Nome com extensão, ex.: 'planta.dxf', 'peca.dwg', 'folha.pdf', 'croqui.png'. Obrigatório com content_*."))] = None,
        content_text: Annotated[str | None, Field(description=t(
            "DXF content as text. A minimal R12 DXF (ENTITIES section only) is enough.",
            "Conteúdo de um DXF em texto. Um DXF R12 mínimo (só a seção ENTITIES) basta."))] = None,
        content_base64: Annotated[str | None, Field(description=t("Binary content in base64 (DWG, PDF, PNG, JPG...).",
                                                                  "Conteúdo binário em base64 (DWG, PDF, PNG, JPG...)."))] = None,
        source_path: Annotated[str | None, Field(description=t(
            "OR the path of a file ALREADY on this PC (e.g. C:\\Users\\...\\Downloads\\plan.pdf).",
            "OU o caminho de um arquivo que JÁ ESTÁ neste PC (ex.: C:\\Users\\...\\Downloads\\planta.pdf)."))] = None,
        action: Annotated[Action, Field(description=t(
            "auto = DXF/DWG exploded into the active drawing (or opened if none), PDF imported as geometry, image "
            "attached; insert_exploded; insert_block = one block; open = as a new drawing; attach_image = reference "
            "image; import_pdf = PDF vectors as entities; save_only = just store on the PC.",
            "auto = DXF/DWG explodido no desenho ativo (ou aberto, se não houver), PDF importado como geometria, "
            "imagem anexada; insert_exploded; insert_block = bloco único; open = como desenho novo; attach_image = "
            "imagem de referência; import_pdf = vetores do PDF como entidades; save_only = só gravar no PC."))] = "auto",
        insertion: Annotated[Pt, Field(description=t("Where to insert (the file's base point goes here).",
                                                     "Onde inserir (ponto base do arquivo vai para cá)."))] = [0.0, 0.0],
        scale: Annotated[float, Field(gt=0, description=t(
            "DXF/DWG/PDF: scale. Image: image WIDTH in drawing units.",
            "DXF/DWG/PDF: escala. Imagem: LARGURA da imagem em unidades do desenho."))] = 1.0,
        rotation_deg: float = 0.0,
        page: Annotated[int, Field(ge=1, description=t("PDF page.", "Página do PDF."))] = 1,
        layer: Annotated[str | None, Field(description=t("Put everything on this layer. Empty = keep the file's layers.",
                                                         "Pôr tudo neste layer. Vazio = manter os layers do arquivo."))] = None,
    ) -> dict[str, Any]:
        """Brings a file into AutoCAD: stores it on this PC and opens it, inserts it into the active drawing,
        imports a vector PDF's geometry (real measurements) or attaches a reference image. Use it when a file
        already exists — your code sandbox is in the cloud and can NOT reach AutoCAD, so send the content here.
        To draw something new, prefer draw_batch. Received files go to Documents\\AutoCAD MCP\\received (never
        overwritten)."""
        path = receive(filename, content_text, content_base64, source_path)
        check = validate(path)
        ext = path.suffix.lower()
        out: dict[str, Any] = {"file": str(path), "check": check}
        if action == "save_only":
            out["action"] = "save_only"
            return out

        def f(app: Any) -> dict[str, Any]:
            act = action
            if act == "auto":
                if ext in IMG_EXT:
                    act = "attach_image"
                elif ext == ".pdf":
                    act = "import_pdf"
                else:
                    act = "insert_exploded" if app.Documents.Count else "open"
            out["action"] = act

            if act == "open":
                if ext not in CAD_EXT:
                    raise AcadError(t("open only works for .dxf/.dwg.", "open serve só para .dxf/.dwg."))
                d = _find_doc_by_path(app, path) or app.Documents.Open(str(path))
                d.Activate()
                out["opened"] = {"name": d.Name, "modelspace_entities": d.ModelSpace.Count}
                return out

            target = active_doc(app)
            target_name = target.Name

            if act == "import_pdf":
                if ext != ".pdf":
                    raise AcadError(t("import_pdf only works for .pdf.", "import_pdf serve só para .pdf."))
                ms = target.ModelSpace
                before = ms.Count
                p = str(path).replace("\\", "/")
                x, y = insertion[0], insertion[1]
                cmd = f'_.-PDFIMPORT\n_F\n"{p}"\n{page}\n{x},{y}\n{scale}\n{rotation_deg}\n'
                info = C.run_command(app, target, cmd, ctx.cfg.command_timeout)
                new = [ms.Item(i) for i in range(before, ms.Count)]
                if layer and new:
                    with undo_group(target):
                        for e in new:
                            apply_props(app, target, e, layer)
                out.update(imported_entities=len(new), handles=[e.Handle for e in new[:200]],
                           extents=_extents(new[:2000]), seconds=info.get("seconds"))
                if not new:
                    out["note"] = t(
                        "Nothing was imported. The PDF may be a scan (image only): try trace_image, or the page "
                        "number may be wrong. Last prompt: {p}",
                        "Nada foi importado. O PDF pode ser um scan (só imagem): tente trace_image, ou a página está "
                        "errada. Último prompt: {p}", p=info.get("last_prompt"))
                else:
                    out["note"] = t(
                        "PDF layers arrive as PDF_<name>. Scale: PDFs plotted by AutoCAD came back at real size in "
                        "tests; PDFs without CAD scale info come in INCHES (1 PDF point = 1/72) — on a mm drawing "
                        "rescale by 25.4. Always check one known measurement and fix with transform_entities "
                        "(operation='scale').",
                        "Os layers do PDF chegam como PDF_<nome>. Escala: PDFs plotados pelo AutoCAD voltaram no "
                        "tamanho real nos testes; PDFs sem informação de escala de CAD entram em POLEGADAS (1 ponto "
                        "= 1/72) — em desenho em mm, reescale por 25,4. Sempre confira uma medida conhecida e corrija "
                        "com transform_entities (operation='scale').")
                return out

            if act == "attach_image":
                if ext not in IMG_EXT:
                    raise AcadError(t("attach_image only works for images.", "attach_image serve só para imagens."))
                with undo_group(target):
                    img = target.ModelSpace.AddRaster(str(path), P(insertion), float(scale), rad(rotation_deg))
                    apply_props(app, target, img, layer)
                info = entity_info(img, with_bbox=True)
                info.update(width=img.ImageWidth, height=img.ImageHeight)
                out["image"] = info
                out["note"] = t(
                    "The image stays LINKED to {p} (do not delete it). Use it as a reference to draw over; "
                    "get_view_image(grid=true) helps read coordinates.",
                    "A imagem fica VINCULADA ao arquivo {p} (não apague). Use-a como referência para desenhar por "
                    "cima; get_view_image(grid=true) ajuda a ler coordenadas.", p=path)
                return out

            if ext not in CAD_EXT:
                raise AcadError(t("insert_* only works for .dxf/.dwg; for images use attach_image, for PDF import_pdf.",
                                  "insert_* serve só para .dxf/.dwg; para imagem use attach_image, para PDF import_pdf."))
            dwg = dxf_to_dwg(app, path) if ext == ".dxf" else path
            if ext == ".dxf":
                out["converted_to"] = str(dwg)
            for i in range(app.Documents.Count):  # the conversion opened/closed another drawing
                if app.Documents.Item(i).Name == target_name:
                    target = app.Documents.Item(i)
                    target.Activate()
                    break
            sc = float(scale)
            with undo_group(target):
                br = target.ModelSpace.InsertBlock(P(insertion), str(dwg), sc, sc, sc, rad(rotation_deg))
                if act == "insert_block":
                    apply_props(app, target, br, layer)
                    out["block"] = entity_info(br, with_bbox=True)
                    return out
                parts = list(br.Explode())
                block_name = br.Name
                br.Delete()
                if layer:
                    for p in parts:
                        apply_props(app, target, p, layer)
            out["inserted_entities"] = len(parts)
            out["handles"] = [p.Handle for p in parts[:200]]
            if len(parts) > 200:
                out["handles_note"] = t("showing 200 of {n}; use query_entities for the rest",
                                        "mostrando 200 de {n}; use query_entities para o resto", n=len(parts))
            out["extents"] = _extents(parts[:2000])
            out["note"] = t("An unused block definition '{b}' remains (purge_and_audit removes it). Check with "
                            "get_view_image.",
                            "Ficou uma definição de bloco '{b}' sem uso (purge_and_audit remove). Confira com "
                            "get_view_image.", b=block_name)
            return out

        return ctx.run(f)

    @ctx.tool(RO, title=t("Received files", "Arquivos recebidos"), pt=(
        "Lista os arquivos já recebidos em Documentos\\AutoCAD MCP\\received (para reusar com source_path)."))
    def list_received_files() -> dict[str, Any]:
        """Lists the files already received in Documents\\AutoCAD MCP\\received (to reuse with source_path)."""
        if not RECEIVED_DIR.is_dir():
            return {"folder": str(RECEIVED_DIR), "files": []}
        files = sorted(RECEIVED_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        return {"folder": str(RECEIVED_DIR),
                "files": [{"name": p.name, "path": str(p), "kb": round(p.stat().st_size / 1024, 1)}
                          for p in files[:100] if p.is_file()]}
