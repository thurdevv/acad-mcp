"""Layers, text styles, linetypes and current settings."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pywintypes
from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import color_out, ensure_linetype, parse_lineweight, rad, set_color, undo_group
from ..i18n import t
from .common import ADD, EDIT, Color, Ctx


def layer_out(ly: Any, current: str) -> dict[str, Any]:
    return {
        "name": ly.Name, "color": color_out(ly), "linetype": ly.Linetype, "lineweight": ly.Lineweight,
        "on": bool(ly.LayerOn), "frozen": bool(ly.Freeze), "locked": bool(ly.Lock),
        "plottable": bool(ly.Plottable), "current": ly.Name == current,
    }


def configure_layer(app: Any, doc: Any, ly: Any, color=None, linetype=None, lineweight=None,
                    on=None, frozen=None, locked=None, plottable=None, description=None) -> None:
    if color is not None:
        set_color(app, ly, color)
    if linetype:
        ensure_linetype(doc, linetype)
        ly.Linetype = linetype
    if lineweight is not None:
        ly.Lineweight = parse_lineweight(lineweight)
    if on is not None:
        ly.LayerOn = bool(on)
    if frozen is not None:
        if frozen and ly.Name == doc.ActiveLayer.Name:
            raise AcadError(t("Cannot freeze the current layer. Change the current layer first.",
                              "Não dá para congelar o layer atual. Mude o layer atual antes."))
        ly.Freeze = bool(frozen)
    if locked is not None:
        ly.Lock = bool(locked)
    if plottable is not None:
        ly.Plottable = bool(plottable)
    if description is not None:
        ly.Description = description


def register(ctx: Ctx) -> None:
    @ctx.tool(EDIT, title=t("Manage layer", "Gerenciar layer"), pt=(
        "Cria, altera (cor, tipo de linha, espessura, ligar/desligar, congelar, travar, plotar), torna atual, renomeia "
        "ou apaga um layer. Apagar só funciona se o layer estiver vazio."))
    def manage_layer(
        name: Annotated[str, Field(description=t("Layer name.", "Nome do layer."))],
        action: Annotated[
            Literal["create_or_update", "set_current", "rename", "delete"],
            Field(description=t("create_or_update creates it if missing and applies the given properties.",
                                "create_or_update cria se não existir e aplica as propriedades informadas.")),
        ] = "create_or_update",
        color: Color = None,
        linetype: Annotated[str | None, Field(description=t(
            "E.g. Continuous, DASHED, CENTER, HIDDEN (loaded if needed).",
            "Ex.: Continuous, DASHED, CENTER, HIDDEN (carregado se preciso)."))] = None,
        lineweight: Annotated[float | str | None, Field(description=t("mm (e.g. 0.35) or 'default'.",
                                                                      "mm (ex.: 0.35) ou 'default'."))] = None,
        on: bool | None = None,
        frozen: bool | None = None,
        locked: bool | None = None,
        plottable: bool | None = None,
        description: str | None = None,
        new_name: Annotated[str | None, Field(description=t("For action='rename'.", "Para action='rename'."))] = None,
        make_current: Annotated[bool, Field(description=t("Also make it current after create/update.",
                                                          "Também tornar atual após criar/atualizar."))] = False,
    ) -> dict[str, Any]:
        """Creates, changes (color, linetype, lineweight, on/off, freeze, lock, plot), makes current, renames or
        deletes a layer. Deleting only works on an empty layer."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            try:
                ly = doc.Layers.Item(name)
                exists = True
            except pywintypes.com_error:
                ly, exists = None, False
            with undo_group(doc):
                if action == "create_or_update":
                    if not exists:
                        ly = doc.Layers.Add(name)
                    configure_layer(app, doc, ly, color, linetype, lineweight, on, frozen, locked, plottable, description)
                    if make_current:
                        doc.ActiveLayer = ly
                    return {"created": not exists, "layer": layer_out(ly, doc.ActiveLayer.Name)}
                if not exists:
                    raise AcadError(t("Layer '{n}' does not exist.", "Layer '{n}' não existe.", n=name))
                if action == "set_current":
                    if ly.Freeze:
                        ly.Freeze = False
                    doc.ActiveLayer = ly
                    return {"current": name}
                if action == "rename":
                    if not new_name:
                        raise AcadError(t("Give new_name.", "Informe new_name."))
                    if name in ("0", "Defpoints"):
                        raise AcadError(t("Layers '0' and 'Defpoints' cannot be renamed.",
                                          "Os layers '0' e 'Defpoints' não podem ser renomeados."))
                    ly.Name = new_name
                    return {"renamed": {"from": name, "to": new_name}}
                if action == "delete":
                    if name == doc.ActiveLayer.Name:
                        raise AcadError(t("Cannot delete the current layer.", "Não dá para apagar o layer atual."))
                    try:
                        ly.Delete()
                    except pywintypes.com_error:
                        raise AcadError(t(
                            "Layer '{n}' is in use (it has entities or is used inside blocks). Move or erase the "
                            "entities first, or use purge_and_audit.",
                            "Layer '{n}' está em uso (tem entidades ou é usado em blocos). Mova ou apague as "
                            "entidades antes, ou use purge_and_audit.", n=name)) from None
                    return {"deleted": name}
            raise AcadError(t("Invalid action.", "action inválida."))

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Text style", "Estilo de texto"), pt=(
        "Cria ou altera um estilo de texto (fonte, altura, fator de largura, inclinação)."))
    def manage_text_style(
        name: str,
        font_file: Annotated[str | None, Field(description=t("Font: 'romans.shx', 'simplex.shx', 'arial.ttf'...",
                                                             "Fonte: 'romans.shx', 'simplex.shx', 'arial.ttf'..."))] = None,
        height: Annotated[float | None, Field(ge=0, description=t("Fixed height (0 = variable, recommended).",
                                                                  "Altura fixa (0 = variável, recomendado)."))] = None,
        width_factor: Annotated[float | None, Field(gt=0)] = None,
        oblique_deg: float | None = None,
        make_current: bool = False,
    ) -> dict[str, Any]:
        """Creates or changes a text style (font, height, width factor, oblique angle)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            try:
                st = doc.TextStyles.Item(name)
                created = False
            except pywintypes.com_error:
                st = doc.TextStyles.Add(name)
                created = True
            with undo_group(doc):
                if font_file:
                    try:
                        st.fontFile = font_file
                    except pywintypes.com_error:
                        raise AcadError(t("AutoCAD could not find the font '{f}'.",
                                          "Fonte '{f}' não encontrada pelo AutoCAD.", f=font_file)) from None
                if height is not None:
                    st.Height = float(height)
                if width_factor is not None:
                    st.Width = float(width_factor)
                if oblique_deg is not None:
                    st.ObliqueAngle = rad(oblique_deg)
                if make_current:
                    doc.ActiveTextStyle = st
            return {"created": created, "name": st.Name, "font_file": st.fontFile, "height": st.Height,
                    "width_factor": st.Width}

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Set current", "Definir atuais"), pt=(
        "Define layer, estilo de texto, estilo de cota, altura de texto, escalas e unidade atuais."))
    def set_current(
        layer: str | None = None,
        text_style: str | None = None,
        dim_style: str | None = None,
        text_size: Annotated[float | None, Field(gt=0, description=t("Default text height (TEXTSIZE).",
                                                                     "Altura padrão de texto (TEXTSIZE)."))] = None,
        ltscale: Annotated[float | None, Field(gt=0, description=t("Global linetype scale (LTSCALE).",
                                                                   "Escala global de tipo de linha (LTSCALE)."))] = None,
        dimscale: Annotated[float | None, Field(gt=0, description=t("Global dimension scale (DIMSCALE).",
                                                                    "Escala global de cotas (DIMSCALE)."))] = None,
        units: Annotated[Literal["mm", "cm", "m", "inch", "feet", "none"] | None,
                         Field(description=t("Insertion units (INSUNITS).", "Unidade de inserção (INSUNITS)."))] = None,
    ) -> dict[str, Any]:
        """Sets the current layer, text style, dimension style, text height, scales and units."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            with undo_group(doc):
                return apply_current(doc)

        def apply_current(doc: Any) -> dict[str, Any]:
            done: dict[str, Any] = {}
            try:
                if layer:
                    doc.ActiveLayer = doc.Layers.Item(layer)
                    done["layer"] = layer
                if text_style:
                    doc.ActiveTextStyle = doc.TextStyles.Item(text_style)
                    done["text_style"] = text_style
                if dim_style:
                    doc.ActiveDimStyle = doc.DimStyles.Item(dim_style)
                    done["dim_style"] = dim_style
            except pywintypes.com_error:
                raise AcadError(t("Name not found. Check with list_layers / list_styles.",
                                  "Nome não encontrado. Confira com list_layers / list_styles.")) from None
            if text_size:
                doc.SetVariable("TEXTSIZE", float(text_size))
                done["text_size"] = text_size
            if ltscale:
                doc.SetVariable("LTSCALE", float(ltscale))
                done["ltscale"] = ltscale
            if dimscale:
                doc.SetVariable("DIMSCALE", float(dimscale))
                done["dimscale"] = dimscale
            if units:
                code = {"none": 0, "inch": 1, "feet": 2, "mm": 4, "cm": 5, "m": 6}[units]
                doc.SetVariable("INSUNITS", code)
                done["units"] = units
            if not done:
                raise AcadError(t("Nothing to change: give at least one parameter.",
                                  "Nada para alterar: informe pelo menos um parâmetro."))
            return {"set": done}

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Load linetype", "Carregar tipo de linha"), pt=(
        "Carrega um tipo de linha de acadiso.lin/acad.lin para poder usá-lo."))
    def load_linetype(
        name: Annotated[str, Field(description="DASHED, CENTER, HIDDEN, PHANTOM, DASHDOT, ACAD_ISO02W100...")],
    ) -> dict[str, Any]:
        """Loads a linetype from acadiso.lin/acad.lin so it can be used."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            with undo_group(doc):
                ensure_linetype(doc, name)
            return {"loaded": name}

        return ctx.doc(f)
