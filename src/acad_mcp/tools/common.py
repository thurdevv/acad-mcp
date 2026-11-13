"""Shared pieces for the tools: context, annotations, input types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from ..acad.util import active_doc
from ..acad.worker import ComWorker
from ..config import Config
from ..i18n import LANG, t

T = TypeVar("T")

Pt = Annotated[
    list[float],
    Field(min_length=2, max_length=3, description=t("Point [x, y] or [x, y, z], in drawing units.",
                                                    "Ponto [x, y] ou [x, y, z], nas unidades do desenho.")),
]
Handles = Annotated[
    list[str],
    Field(min_length=1, description=t("Entity handles (e.g. ['2A3', '2A4']) from query_entities.",
                                      "Handles das entidades (ex.: ['2A3', '2A4']), obtidos em query_entities.")),
]
Layer = Annotated[str | None, Field(description=t("Layer for the entity (created if missing). Empty = current layer.",
                                                  "Layer da entidade (criado se não existir). Vazio = layer atual."))]
Color = Annotated[
    int | str | None,
    Field(description=t(
        "Color: ACI 1-255 (1 red, 2 yellow, 3 green, 4 cyan, 5 blue, 6 magenta, 7 white), 'bylayer', "
        "'byblock', '#RRGGBB' or 'R,G,B'. Empty = ByLayer.",
        "Cor: ACI 1-255 (1 vermelho, 2 amarelo, 3 verde, 4 ciano, 5 azul, 6 magenta, 7 branco), "
        "'bylayer', 'byblock', '#RRGGBB' ou 'R,G,B'. Vazio = ByLayer.",
    )),
]

RO = ToolAnnotations(read_only_hint=True, open_world_hint=False)
VIEW = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
ADD = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


@dataclass
class Ctx:
    mcp: MCPServer
    cfg: Config
    worker: ComWorker

    def run(self, fn: Callable[[Any], T]) -> T:
        """fn(app) on the COM thread."""
        return self.worker.call(fn)

    def doc(self, fn: Callable[[Any, Any], T]) -> T:
        """fn(app, active_document) on the COM thread."""
        return self.worker.call(lambda app: fn(app, active_doc(app)))

    def tool(
        self,
        annotations: ToolAnnotations,
        title: str | None = None,
        name: str | None = None,
        structured: bool | None = None,
        enabled: bool = True,
        pt: str | None = None,
    ):
        """Register the tool unless it writes and the server is read-only.

        The docstring is the English description; `pt` replaces it when ACAD_MCP_LANG=pt.
        """
        writes = not annotations.read_only_hint

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled or (writes and self.cfg.read_only):
                return fn
            desc = pt if (LANG == "pt" and pt) else None
            ann = annotations.model_copy(update={"title": title}) if title else annotations
            self.mcp.add_tool(fn, name=name, title=title, description=desc, annotations=ann,
                              structured_output=structured)
            return fn

        return deco
