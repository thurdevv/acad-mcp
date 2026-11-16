"""Layouts (sheets): list, create / copy / rename / delete, page setup, viewports and one PDF per sheet.

Measured on AutoCAD 2027:
- A layout's block is empty until the layout is activated once; then it holds the paper-space viewport (first
  item) and, depending on the user's options, one automatic viewport.
- A new layout comes with the Windows default printer and no paper: set the page before plotting.
- Viewport scale = CustomScale. The view centre can only be set from inside the viewport (MSpace + ZoomCenter);
  changing DXF group 12 with entmod has no effect.
- Plot.PlotToFile writes ONE file per call (two layouts in one call wrote nothing), runs in the foreground only
  with BACKGROUNDPLOT = 0, and leaves the plotted layout active.
- CenterPlot is refused when the plot area is "layout".
"""

from __future__ import annotations

import os
import re
import time
from typing import Annotated, Any, Literal

import pythoncom
import pywintypes
import win32con
import win32gui
from pydantic import Field
from win32com.client import VARIANT

from ..acad import commands as C
from ..acad.errors import AcadError
from ..acad.util import P, _g, apply_props, pt_out, rnd, select, undo_group
from ..i18n import t
from .common import ADD, EDIT, RO, Ctx, Layer, Pt

PDF_DEVICE = "DWG To PDF.pc3"
PLOT_AREAS = {"display": 0, "extents": 1, "limits": 2, "layout": 5}
AREA_NAMES = {0: "display", 1: "extents", 2: "limits", 3: "view", 4: "window", 5: "layout"}
BAD_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

Ratio = Annotated[float | str | None, Field(description=t(
    "Scale as '1:50' (paper : drawing units) or as a number (1:50 = 0.02).",
    "Escala como '1:50' (papel : unidades do desenho) ou como número (1:50 = 0.02)."))]


# ------------------------------------------------------------------ helpers


def parse_ratio(scale: float | str | None) -> float | None:
    """'1:50' -> 0.02, '2:1' -> 2.0, 0.02 -> 0.02. None stays None."""
    if scale is None:
        return None
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        v = float(scale)
    else:
        s = str(scale).strip().replace(",", ".").replace("/", ":")
        try:
            if ":" in s:
                a, b = s.split(":", 1)
                v = float(a) / float(b)
            else:
                v = float(s)
        except (ValueError, ZeroDivisionError):
            raise AcadError(t("Scale '{s}' not understood. Use '1:50' or a number like 0.02.",
                              "Não entendi a escala '{s}'. Use '1:50' ou um número como 0.02.", s=scale)) from None
    if not v > 0:
        raise AcadError(t("Scale must be greater than zero.", "A escala precisa ser maior que zero."))
    return v


def ratio_text(v: float) -> str:
    """0.02 -> '1:50', 2 -> '2:1', otherwise the number."""
    if v <= 0:
        return str(v)
    if v <= 1 and abs(1 / v - round(1 / v)) < 1e-6:
        return f"1:{round(1 / v)}"
    if v > 1 and abs(v - round(v)) < 1e-6:
        return f"{round(v)}:1"
    return f"{rnd(v)}"


def pick_media(pairs: list[tuple[str, str]], paper: str, landscape: bool = True) -> str | None:
    """Choose a paper from (canonical, local name) pairs. `paper` is a full name or a size such as 'A3'.

    For a size, prefers the borderless ('full bleed') paper, then the plain one, in the wanted orientation when
    the name carries its dimensions, e.g. 'ISO full bleed A3 (420.00 x 297.00 MM)'.
    """
    want = paper.strip().lower()
    for canon, local in pairs:
        if want in (canon.lower(), local.lower()):
            return canon
    token = re.compile(r"(?<![a-z0-9])" + re.escape(want) + r"(?![a-z0-9])")
    best: tuple[int, str] | None = None
    for canon, local in pairs:
        name = local.lower()
        if not token.search(name) and not token.search(canon.lower().replace("_", " ")):
            continue
        score = 0
        if "full bleed" in name:
            score += 4
        elif "expand" in name or "oversize" in name:
            score += 0
        else:
            score += 2
        m = re.search(r"\(\s*([\d.]+)\s*x\s*([\d.]+)", name)
        if m and (float(m.group(1)) >= float(m.group(2))) == landscape:
            score += 8
        if best is None or score > best[0]:
            best = (score, canon)
    return best[1] if best else None


def pdf_page_mm(path: str) -> list[float] | None:
    """Page size of the first page of a PDF, in whole millimetres (from its MediaBox). None if not found.

    AutoCAD writes the MediaBox in whole points (A3 = 1191 x 842 pt = 420.2 x 297.0 mm), hence the rounding.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(2_000_000)
    except OSError:
        return None
    m = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", data)
    if not m:
        return None
    x0, y0, x1, y1 = (float(g) for g in m.groups())
    return [float(round((x1 - x0) * 25.4 / 72)), float(round((y1 - y0) * 25.4 / 72))]


def safe_file_part(name: str) -> str:
    return BAD_FILE_CHARS.sub("_", name).strip(" .") or "layout"


def _bstrs(items: list[str]) -> VARIANT:
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, list(items))


def _bubbles(main_hwnd: int) -> set[int]:
    """Notification bubbles currently shown inside the AutoCAD window."""
    found: set[int] = set()

    def cb(h: int, _: Any) -> bool:
        try:
            if win32gui.GetClassName(h) == "BubbleWindowClass" and win32gui.IsWindowVisible(h):
                found.add(h)
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        win32gui.EnumChildWindows(main_hwnd, cb, None)
    except Exception:  # noqa: BLE001
        pass
    return found


def close_new_bubbles(main_hwnd: int, before: set[int], wait: float = 1.5) -> int:
    """Close the "Plot and Publish Job Complete" bubble(s) our own plot caused.

    Measured on AutoCAD 2027: every plot leaves a bubble over the corner of the drawing area that stays until
    someone clicks it (so it shows up in later screenshots); it appears a moment AFTER PlotToFile returns, and
    TRAYNOTIFY = 0 during the plot does not stop it. Only bubbles that were not there before are closed.
    """
    known = set(before)
    closed = 0
    last = time.monotonic()
    end = last + wait
    while time.monotonic() < end:
        new = _bubbles(main_hwnd) - known
        if new:
            for h in new:
                try:
                    win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                except Exception:  # noqa: BLE001
                    pass
            known |= new
            closed += len(new)
            last = time.monotonic()
        elif closed and time.monotonic() - last > 0.4:
            break
        time.sleep(0.1)
    return closed


def layouts_in_order(doc: Any) -> list[Any]:
    items = [doc.Layouts.Item(i) for i in range(doc.Layouts.Count)]
    return sorted(items, key=lambda lay: lay.TabOrder)


def get_layout(doc: Any, name: str) -> Any:
    for lay in layouts_in_order(doc):
        if lay.Name.upper() == name.strip().upper():
            return lay
    raise AcadError(t("Layout '{n}' does not exist. Layouts: {a}", "A folha (layout) '{n}' não existe. Folhas: {a}",
                      n=name, a=[lay.Name for lay in layouts_in_order(doc)]))


def viewport_info(pv: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"handle": pv.Handle, "center": pt_out(pv.Center, 2), "width": rnd(pv.Width),
                         "height": rnd(pv.Height), "layer": pv.Layer}
    sc = _g(pv, "CustomScale")
    if sc:
        d["scale"] = rnd(sc, 8)
        d["scale_text"] = ratio_text(float(sc))
    d["locked"] = bool(_g(pv, "DisplayLocked", False))
    return d


def paper_viewport_handle(lay: Any) -> str | None:
    """Handle of the paper-space viewport itself (first item of an activated layout), which is not a 'window'."""
    try:
        blk = lay.Block
        if blk.Count and blk.Item(0).ObjectName == "AcDbViewport":
            return blk.Item(0).Handle
    except pywintypes.com_error:
        pass
    return None


def layout_info(doc: Any, lay: Any, viewports: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": lay.Name, "tab_order": lay.TabOrder, "current": lay.Name == doc.ActiveLayout.Name,
        "model": bool(lay.ModelType), "device": lay.ConfigName, "paper": lay.CanonicalMediaName,
    }
    try:
        w, h = lay.GetPaperSize()
        d["paper_size_mm"] = [rnd(w, 2), rnd(h, 2)]
    except pywintypes.com_error:
        pass
    d["rotation_deg"] = int(_g(lay, "PlotRotation", 0)) * 90
    d["plot_style"] = _g(lay, "StyleSheet", "")
    d["plot_area"] = AREA_NAMES.get(_g(lay, "PlotType"), "?")
    try:
        if lay.UseStandardScale and lay.StandardScale == 0:
            d["plot_scale"] = "fit"
        else:
            num, den = lay.GetCustomScale()
            d["plot_scale"] = f"{num:g}:{den:g}"
    except pywintypes.com_error:
        pass
    count = lay.Block.Count
    if not lay.ModelType:
        skip = paper_viewport_handle(lay)
        d["entities"] = count - (1 if skip else 0)
        if viewports:
            vps = [e for e in select(doc, types=["VIEWPORT"], layout=lay.Name) if e.Handle != skip] if count else []
            d["viewports"] = [viewport_info(e) for e in vps]
        if not count:
            d["note"] = t("never opened: its content is created when the layout is activated",
                          "nunca aberta: o conteúdo é criado quando a folha é ativada")
    else:
        d["entities"] = count
    return d


def apply_page_setup(lay: Any, device: str | None, paper: str | None, orientation: str | None,
                     plot_style: str | None, plot_area: str | None, scale: float | str | None) -> list[str]:
    notes: list[str] = []
    dev = device
    if dev is None and paper and "pdf" not in (lay.ConfigName or "").lower():
        dev = PDF_DEVICE
        notes.append(t("device set to '{d}' (the layout had '{o}')", "dispositivo definido como '{d}' (a folha estava com '{o}')",
                       d=PDF_DEVICE, o=lay.ConfigName))
    if dev:
        lay.RefreshPlotDeviceInfo()
        names = list(lay.GetPlotDeviceNames())
        match = next((n for n in names if n.lower() == dev.strip().lower()), None)
        if match is None:
            raise AcadError(t("Plot device '{d}' not found. Devices: {a}", "Dispositivo de plotagem '{d}' não encontrado. "
                              "Dispositivos: {a}", d=dev, a=names))
        lay.ConfigName = match
        lay.RefreshPlotDeviceInfo()
    if paper:
        canon = list(lay.GetCanonicalMediaNames())
        pairs = [(m, lay.GetLocaleMediaName(m)) for m in canon]
        chosen = pick_media(pairs, paper, landscape=(orientation != "portrait"))
        if chosen is None:
            raise AcadError(t("Paper '{p}' not found for device '{d}'. Some papers: {a}",
                              "Papel '{p}' não encontrado no dispositivo '{d}'. Alguns papéis: {a}",
                              p=paper, d=lay.ConfigName, a=[loc for _, loc in pairs][:40]))
        lay.CanonicalMediaName = chosen
    if paper or orientation:
        w, h = lay.GetPaperSize()
        landscape = orientation != "portrait"
        lay.PlotRotation = 0 if (w >= h) == landscape else 1
    if plot_style is not None:
        if plot_style.strip().lower() in ("", "none"):
            lay.StyleSheet = ""
        else:
            names = list(lay.GetPlotStyleTableNames())
            match = next((n for n in names if n.lower() == plot_style.strip().lower()), None)
            if match is None:
                raise AcadError(t("Plot style '{s}' not found. Available: {a}",
                                  "Estilo de plotagem '{s}' não encontrado. Disponíveis: {a}", s=plot_style, a=names))
            try:
                lay.StyleSheet = match
            except pywintypes.com_error as exc:
                raise AcadError(t("AutoCAD refused the plot style '{s}' (a drawing uses either .ctb or .stb): {e}",
                                  "O AutoCAD recusou o estilo de plotagem '{s}' (um desenho usa .ctb ou .stb): {e}",
                                  s=match, e=exc.args[1])) from None
    if plot_area:
        if lay.ModelType and plot_area == "layout":
            raise AcadError(t("The Model tab has no 'layout' area: use extents, limits or display.",
                              "A aba Model não tem a área 'layout': use extents, limits ou display."))
        lay.PlotType = PLOT_AREAS[plot_area]
        if plot_area != "layout":
            try:
                lay.CenterPlot = True
            except pywintypes.com_error:
                pass
    if scale is not None:
        try:
            if str(scale).strip().lower() == "fit":
                lay.UseStandardScale = True
                lay.StandardScale = 0  # acScaleToFit
            else:
                v = parse_ratio(scale)
                lay.UseStandardScale = False
                if v <= 1:
                    lay.SetCustomScale(1.0, round(1.0 / v, 8))
                else:
                    lay.SetCustomScale(round(v, 8), 1.0)
        except pywintypes.com_error as exc:
            raise AcadError(t("AutoCAD refused the plot scale '{s}' for this plot area ({e}). 'fit' is not allowed "
                              "with the 'layout' area.", "O AutoCAD recusou a escala de plotagem '{s}' para esta área "
                              "({e}). 'fit' não vale com a área 'layout'.", s=scale, e=exc.args[1])) from None
    return notes


# ------------------------------------------------------------------ tools


def register(ctx: Ctx) -> None:
    @ctx.tool(RO, title=t("List layouts", "Listar folhas (layouts)"), pt=(
        "Lista as folhas (layouts) do desenho na ordem das abas: papel, dispositivo, escala de plotagem, "
        "quantidade de entidades e os viewports (centro, tamanho, escala, travado)."))
    def list_layouts() -> dict[str, Any]:
        """Lists the drawing's layouts (sheets) in tab order: paper, device, plot scale, entity count and the
        viewports (centre, size, scale, locked)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            lays = layouts_in_order(doc)
            return {"current": doc.ActiveLayout.Name, "count": len(lays),
                    "layouts": [layout_info(doc, lay) for lay in lays]}

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Manage layout", "Gerenciar folha (layout)"), pt=(
        "Cria, copia, renomeia, apaga ou ativa uma folha (layout) e define a página dela: papel ('A3', 'A1' ou o "
        "nome completo), orientação, dispositivo, estilo de plotagem (.ctb), área e escala de plotagem. "
        "action='setup' só muda a página. Uma folha nova vem com a impressora padrão do Windows e sem papel: "
        "informe paper para deixá-la pronta para PDF."))
    def manage_layout(
        action: Annotated[Literal["create", "copy", "rename", "delete", "activate", "setup"], Field(description=t(
            "create / copy (needs new_name) / rename (needs new_name) / delete / activate (make it current) / "
            "setup (only change the page).",
            "create / copy (precisa de new_name) / rename (precisa de new_name) / delete / activate (tornar atual) / "
            "setup (só mudar a página)."))],
        name: Annotated[str, Field(description=t("Layout name ('Model' is the model tab).",
                                                 "Nome da folha ('Model' é a aba do modelo)."))],
        new_name: str | None = None,
        paper: Annotated[str | None, Field(description=t(
            "Paper: a size such as 'A4', 'A3', 'A1', or the full name shown by AutoCAD.",
            "Papel: um tamanho como 'A4', 'A3', 'A1', ou o nome completo que o AutoCAD mostra."))] = None,
        orientation: Literal["landscape", "portrait"] | None = None,
        device: Annotated[str | None, Field(description=t(
            "Plot device. Empty with a paper = 'DWG To PDF.pc3' when the layout is not on a PDF device yet.",
            "Dispositivo de plotagem. Vazio com um papel = 'DWG To PDF.pc3' quando a folha ainda não está num "
            "dispositivo PDF."))] = None,
        plot_style: Annotated[str | None, Field(description=t("Plot style table, e.g. 'monochrome.ctb'; 'none' clears it.",
                                                              "Tabela de estilo de plotagem, ex.: 'monochrome.ctb'; "
                                                              "'none' limpa."))] = None,
        plot_area: Literal["layout", "extents", "limits", "display"] | None = None,
        plot_scale: Annotated[float | str | None, Field(description=t(
            "Plot scale: '1:1' (usual for sheets), '1:50', or 'fit' (not with the 'layout' area).",
            "Escala de plotagem: '1:1' (o normal em folhas), '1:50', ou 'fit' (não vale com a área 'layout')."))] = None,
    ) -> dict[str, Any]:
        """Creates, copies, renames, deletes or activates a layout (sheet) and sets its page: paper ('A3', 'A1' or
        the full name), orientation, device, plot style (.ctb), plot area and plot scale. action='setup' only
        changes the page. A new layout comes with the Windows default printer and no paper: give `paper` to make it
        ready for PDF."""
        setup = (device, paper, orientation, plot_style, plot_area, plot_scale)
        has_setup = any(v is not None for v in setup)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            names = {lay.Name.upper() for lay in layouts_in_order(doc)}
            if action in ("copy", "rename"):
                if not new_name or not new_name.strip():
                    raise AcadError(t("action='{a}' needs new_name.", "action='{a}' precisa de new_name.", a=action))
                if new_name.strip().upper() in names:
                    raise AcadError(t("Layout '{n}' already exists.", "A folha '{n}' já existe.", n=new_name))
            if action == "create" and name.strip().upper() in names:
                raise AcadError(t("Layout '{n}' already exists. Use action='setup' to change its page.",
                                  "A folha '{n}' já existe. Use action='setup' para mudar a página dela.", n=name))
            if action == "setup" and not has_setup:
                raise AcadError(t("Nothing to change: give paper, orientation, device, plot_style, plot_area or "
                                  "plot_scale.", "Nada para alterar: informe paper, orientation, device, plot_style, "
                                  "plot_area ou plot_scale."))
            notes: list[str] = []
            with undo_group(doc):
                try:
                    if action == "create":
                        lay = doc.Layouts.Add(name.strip())
                    elif action == "copy":
                        src = get_layout(doc, name)
                        r = C.run_lisp(app, doc, f'(command "_.-LAYOUT" "_C" {C.lisp_str(src.Name)} '
                                                 f'{C.lisp_str(new_name.strip())})', ctx.cfg.command_timeout)
                        if r.get("error"):
                            raise AcadError(t("Could not copy the layout: {e}", "Não consegui copiar a folha: {e}",
                                              e=r["error"]))
                        lay = get_layout(doc, new_name)
                    else:
                        lay = get_layout(doc, name)
                        if action == "rename":
                            lay.Name = new_name.strip()
                        elif action == "activate":
                            doc.ActiveLayout = lay
                        elif action == "delete":
                            gone = lay.Name
                            lay.Delete()
                            return {"deleted": gone, "layouts": [x.Name for x in layouts_in_order(doc)]}
                    if has_setup:
                        notes += apply_page_setup(lay, *setup)
                except pywintypes.com_error as exc:
                    raise AcadError(t("AutoCAD refused ({a} '{n}'): {e}", "O AutoCAD recusou ({a} '{n}'): {e}",
                                      a=action, n=name, e=(exc.args[2][2] if exc.args[2] else exc.args[1]))) from None
            out = {"action": action, **layout_info(doc, lay)}
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Create viewport", "Criar viewport"), pt=(
        "Cria um viewport (janela para o modelo) numa folha, com a escala e o ponto do modelo que deve ficar no "
        "centro. center, width e height estão em unidades do papel (mm). Sem scale, o viewport mostra o desenho "
        "todo. A folha fica ativa depois."))
    def create_viewport(
        center: Annotated[Pt, Field(description=t("Centre of the viewport on the sheet (paper units, mm).",
                                                  "Centro do viewport na folha (unidades do papel, mm)."))],
        width: Annotated[float, Field(gt=0)],
        height: Annotated[float, Field(gt=0)],
        scale: Ratio = None,
        model_center: Annotated[Pt | None, Field(description=t(
            "Model point shown at the centre of the viewport. Empty = centre of the drawing's extents.",
            "Ponto do modelo que aparece no centro do viewport. Vazio = centro da extensão do desenho."))] = None,
        layout: Annotated[str | None, Field(description=t("Layout name. Empty = the current layout.",
                                                          "Nome da folha. Vazio = a folha atual."))] = None,
        layer: Layer = None,
        locked: Annotated[bool, Field(description=t("Lock the view so zooming inside does not change the scale.",
                                                    "Travar a vista para o zoom dentro dele não mudar a escala."))] = False,
    ) -> dict[str, Any]:
        """Creates a viewport (a window to the model) on a layout, with a scale and the model point that must be at
        its centre. center, width and height are in paper units (mm). Without scale the viewport shows the whole
        drawing. The layout stays active afterwards."""
        sc = parse_ratio(scale)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            lay = get_layout(doc, layout) if layout else doc.ActiveLayout
            if lay.ModelType:
                raise AcadError(t("Viewports go on a sheet, not on the Model tab: pass layout='<name>' "
                                  "(see list_layouts).", "Viewport fica numa folha, não na aba Model: passe "
                                  "layout='<nome>' (veja list_layouts)."))
            shown = None
            with undo_group(doc):
                if doc.ActiveLayout.Name != lay.Name:
                    doc.ActiveLayout = lay
                pv = doc.PaperSpace.AddPViewport(P(center), float(width), float(height))
                pv.Display(True)
                if sc or model_center:
                    target = sc or float(pv.CustomScale)
                    doc.MSpace = True
                    try:
                        doc.ActivePViewport = pv
                        if model_center:
                            app.ZoomCenter(P(model_center), float(height) / target)
                        pv.CustomScale = target
                        shown = pt_out(doc.GetVariable("VIEWCTR"), 2)
                    finally:
                        doc.MSpace = False
                if locked:
                    pv.DisplayLocked = True
                notes = apply_props(app, doc, pv, layer)
            out = {"layout": lay.Name, **viewport_info(pv)}
            if shown:
                out["model_center"] = shown
            if notes:
                out["notes"] = notes
            return out

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Plot layouts to PDF", "Plotar folhas em PDF"), pt=(
        "Gera um PDF por folha (layout), com a página configurada em cada folha, na pasta do desenho ou em "
        "output_folder: <desenho>-<folha>.pdf. Sem layouts = todas as folhas; inclua 'Model' na lista para plotar "
        "o modelo. Não grava por cima de PDF existente sem overwrite=true. Folha que não está num dispositivo PDF "
        "sai com aviso: configure com manage_layout (action='setup', paper='A3')."))
    def plot_layouts(
        layouts: Annotated[list[str] | None, Field(description=t(
            "Layout names. Empty = every sheet (not the Model tab).",
            "Nomes das folhas. Vazio = todas as folhas (sem a aba Model)."))] = None,
        output_folder: Annotated[str | None, Field(description=t(
            "Folder for the PDFs. Empty = the drawing's folder (the drawing must be saved).",
            "Pasta dos PDFs. Vazio = a pasta do desenho (o desenho precisa estar salvo)."))] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Writes one PDF per layout (sheet), using each layout's own page setup, in the drawing's folder or in
        output_folder: <drawing>-<layout>.pdf. No layouts = every sheet; include 'Model' in the list to plot the
        model. Never writes over an existing PDF without overwrite=true. A layout that is not on a PDF device is
        plotted with a warning: set it up with manage_layout (action='setup', paper='A3')."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            if layouts:
                chosen = [get_layout(doc, n) for n in layouts]
            else:
                chosen = [lay for lay in layouts_in_order(doc) if not lay.ModelType]
            if not chosen:
                raise AcadError(t("This drawing has no sheets (layouts) to plot.",
                                  "Este desenho não tem folhas (layouts) para plotar."))
            folder = output_folder or (doc.Path if doc.FullName else None)
            if not folder:
                raise AcadError(t("The drawing was never saved: give output_folder.",
                                  "O desenho nunca foi salvo: informe output_folder."))
            os.makedirs(folder, exist_ok=True)
            stem = os.path.splitext(doc.Name)[0]
            targets = [(lay, os.path.join(folder, f"{stem}-{safe_file_part(lay.Name)}.pdf")) for lay in chosen]
            existing = [p for _, p in targets if os.path.exists(p)]
            if existing and not overwrite:
                raise AcadError(t("These PDFs already exist (use overwrite=true or another folder): {p}",
                                  "Estes PDFs já existem (use overwrite=true ou outra pasta): {p}", p=existing))
            active = doc.ActiveLayout.Name
            background = doc.GetVariable("BACKGROUNDPLOT")
            main_hwnd = int(app.HWND)
            bubbles_before = _bubbles(main_hwnd)
            rows: list[dict[str, Any]] = []
            try:
                doc.SetVariable("BACKGROUNDPLOT", 0)
                plot = doc.Plot
                plot.QuietErrorMode = True
                for lay, path in targets:
                    row: dict[str, Any] = {"layout": lay.Name, "device": lay.ConfigName, "paper": lay.CanonicalMediaName}
                    on_pdf = "pdf" in (lay.ConfigName or "").lower()
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                        plot.SetLayoutsToPlot(_bstrs([lay.Name]))
                        ok = plot.PlotToFile(path) if on_pdf else plot.PlotToFile(path, PDF_DEVICE)
                    except (pywintypes.com_error, OSError) as exc:
                        ok = False
                        row["error"] = str(exc.args[1] if isinstance(exc, pywintypes.com_error) else exc)
                    if ok and os.path.exists(path):
                        row["pdf"] = path
                        row["bytes"] = os.path.getsize(path)
                        size = pdf_page_mm(path)
                        if size:
                            row["page_mm"] = size
                    elif "error" not in row:
                        row["error"] = t("AutoCAD did not write the PDF", "o AutoCAD não gravou o PDF")
                    if not on_pdf:
                        row["warning"] = t(
                            "page setup is for '{d}', not a PDF device: paper and scale may be wrong. Set it with "
                            "manage_layout(action='setup', name='{n}', paper='A3').",
                            "a página está configurada para '{d}', não para PDF: papel e escala podem sair errados. "
                            "Configure com manage_layout(action='setup', name='{n}', paper='A3').",
                            d=lay.ConfigName, n=lay.Name)
                    rows.append(row)
            finally:
                try:
                    doc.SetVariable("BACKGROUNDPLOT", background)
                    if doc.ActiveLayout.Name != active:
                        doc.ActiveLayout = get_layout(doc, active)
                except (pywintypes.com_error, AcadError):
                    pass
                if any("pdf" in r for r in rows):
                    close_new_bubbles(main_hwnd, bubbles_before)
            return {"folder": folder, "written": sum(1 for r in rows if "pdf" in r),
                    "failed": sum(1 for r in rows if "error" in r), "files": rows}

        return ctx.doc(f)
