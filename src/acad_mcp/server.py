"""acad-mcp server."""

from __future__ import annotations

import argparse
import logging
import os
import sys

INSTRUCTIONS_EN = """\
You control the AutoCAD running on the user's computer. Rules:

0. DRAW STRAIGHT INTO AUTOCAD. When the user asks to draw, recreate or copy something (including from an
   attached image, photo, PDF or sketch), read the measurements and draw with `draw_batch` and the other tools.
   Do NOT create files (DXF, DWG, scripts) in your own code sandbox: it lives in the cloud and cannot reach
   AutoCAD or the user's disk. Only when a file already exists, send its CONTENT through `import_file`
   (DXF as text; DWG/PDF/image as base64) or its path if it is on the PC. Vector PDFs from CAD import with
   real measurements (`import_file`, action import_pdf).
1. Start with `status` (and `drawing_info` for units and extents). Everything acts on the ACTIVE drawing.
2. Coordinates and sizes are in drawing units (usually mm). Angles in degrees, 0 = east, counter-clockwise.
3. To edit existing things: find handles with `query_entities` (or `get_selection` if the user selected),
   then use the editing tools with those handles. Queries are fast even on large drawings.
4. To draw many things, use `draw_batch` (one call, one undo).
5. After drawing or editing, check with `get_view_image` (grid=true shows coordinates) and fix what is wrong.
6. Totals and bills of materials: `quantities` and `block_report`; layer standards: `check_standards` /
   `apply_standards`.
7. What has no tool (FILLET, TRIM, EXTEND, JOIN, STRETCH...) do with `run_lisp`
   (e.g. (command "_.FILLET" (handent "2A") (handent "2B"))) or `run_command`.
8. Never write over the user's files unless asked: `save_drawing` with a new name is the default.
9. Each write tool is one undo step; `undo` reverts.
10. If AutoCAD is busy or minimized, tell the user what to do (the error message says how).
"""

INSTRUCTIONS_PT = """\
Você controla o AutoCAD aberto no computador do usuário. Regras:

0. DESENHE DIRETO NO AUTOCAD. Quando o usuário pedir para desenhar, recriar ou copiar algo (inclusive a partir
   de imagem, foto, PDF ou croqui anexado), leia as medidas e desenhe com `draw_batch` e as outras ferramentas.
   NÃO crie arquivos (DXF, DWG, scripts) no seu ambiente de código: ele fica na nuvem e não alcança o AutoCAD
   nem o disco do usuário. Só se já existir um arquivo pronto, mande o CONTEÚDO por `import_file` (DXF em
   texto; DWG/PDF/imagem em base64) ou o caminho, se estiver no PC. PDF vetorial de CAD entra com as medidas
   reais (`import_file`, action import_pdf).
1. Comece com `status` (e `drawing_info` para unidades e extensão). Tudo age no desenho ATIVO.
2. Coordenadas e medidas estão nas unidades do desenho (normalmente mm). Ângulos em graus, 0 = leste, anti-horário.
3. Para editar algo existente: ache os handles com `query_entities` (ou `get_selection` se o usuário selecionou),
   depois use as ferramentas de edição. As buscas são rápidas mesmo em desenhos grandes.
4. Para desenhar muita coisa, use `draw_batch` (uma chamada, um Ctrl+Z).
5. Depois de desenhar ou editar, confira com `get_view_image` (grid=true mostra coordenadas) e corrija.
6. Totais e listas de material: `quantities` e `block_report`; padrão de layers: `check_standards` /
   `apply_standards`.
7. O que não tiver ferramenta (FILLET, TRIM, EXTEND, JOIN, STRETCH...) faça com `run_lisp`
   (ex.: (command "_.FILLET" (handent "2A") (handent "2B"))) ou `run_command`.
8. Nunca grave por cima de arquivos do usuário sem ele pedir: `save_drawing` com nome novo é o padrão.
9. Cada ferramenta de escrita é um passo de desfazer; `undo` desfaz.
10. Se o AutoCAD estiver ocupado ou minimizado, explique ao usuário o que fazer (a mensagem de erro diz).
"""


def build(cfg=None):
    """Build the MCPServer. Imports are local so --lang is applied before any tool text is created."""
    from mcp.server.mcpserver import MCPServer

    from . import __version__
    from .acad import commands, util
    from .acad.winutil import make_dpi_aware
    from .acad.worker import ComWorker
    from .config import Config
    from .i18n import LANG
    from .tools import register_all
    from .tools.common import Ctx

    cfg = cfg or Config.from_env()
    commands.AUTO_RESTORE = cfg.auto_restore
    util.UNDO_MARKER = cfg.undo_marker
    make_dpi_aware()
    mcp = MCPServer(
        name="acad",
        title="AutoCAD",
        instructions=INSTRUCTIONS_PT if LANG == "pt" else INSTRUCTIONS_EN,
        version=__version__,
    )
    worker = ComWorker(busy_timeout=cfg.busy_timeout, launch_if_missing=cfg.launch_if_missing)
    register_all(Ctx(mcp=mcp, cfg=cfg, worker=worker))
    return mcp


def _setup_cli(action: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog=f"acad-mcp {action}-claude",
                                 description=f"{action.title()} acad-mcp in Claude Desktop.")
    ap.add_argument("--lang", choices=["en", "pt"])
    ap.add_argument("--yes", action="store_true", help="Do not ask (closes Claude if needed).")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing.")
    ap.add_argument("--no-reopen", action="store_true", help="Do not reopen Claude at the end.")
    a = ap.parse_args(argv)
    from .setup_claude import run

    return run(action, lang=a.lang, assume_yes=a.yes, dry_run=a.dry_run, reopen=not a.no_reopen,
               interactive=not a.yes)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("install-claude", "uninstall-claude"):
        sys.exit(_setup_cli(argv[0].split("-")[0], argv[1:]))
    if argv and argv[0].lower() in ("diagnostico", "diagnóstico", "diagnose", "diagnostics"):
        if "ACAD_MCP_LANG" not in os.environ:  # a person is reading this: follow the Windows language
            from .setup_claude import ui_is_portuguese

            os.environ["ACAD_MCP_LANG"] = "pt" if ui_is_portuguese() else "en"
        from .diagnose import main as diagnose_main

        sys.exit(diagnose_main(argv[1:]))
    if argv and argv[0] in ("--version", "-V"):
        from . import __version__

        print(f"acad-mcp {__version__}")
        sys.exit(0)
    if not argv and sys.stdin is not None and sys.stdin.isatty():
        # Opened by a person (double-click or typed in a terminal), not by an MCP client: an MCP client
        # always talks through pipes. Offer to connect it to Claude instead of waiting silently.
        from .setup_claude import run

        sys.exit(run("install"))

    ap = argparse.ArgumentParser(prog="acad-mcp", description=(
        "MCP server to drive AutoCAD (Windows, COM). Run with no arguments in a console to connect it to "
        "Claude Desktop; MCP clients start it through stdio."))
    ap.add_argument("--stdio", action="store_true", help="Serve over stdio even when started from a console.")
    ap.add_argument("--http", action="store_true", help="Serve over Streamable HTTP instead of stdio.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lang", choices=["en", "pt"], help="Language of tool descriptions and messages.")
    ap.add_argument("--read-only", action="store_true", help="Read-only tools only.")
    ap.add_argument("--no-lisp", action="store_true", help="Disable run_lisp.")
    ap.add_argument("--no-commands", action="store_true", help="Disable run_command.")
    ap.add_argument("--auto-restore", action="store_true",
                    help="Restore a minimized AutoCAD window when needed (AutoCAD may grab focus).")
    ap.add_argument(
        "--allowed-host", action="append", default=[],
        help="With --http: allow an extra Host header (e.g. a tunnel domain). Repeatable. Only put this behind "
             "authentication: whoever reaches the URL controls AutoCAD.",
    )
    args = ap.parse_args(argv)
    if args.lang:
        os.environ["ACAD_MCP_LANG"] = args.lang  # before i18n is imported

    from .config import Config

    cfg = Config.from_env()
    cfg.read_only |= args.read_only
    cfg.allow_lisp &= not args.no_lisp
    cfg.allow_commands &= not args.no_commands
    cfg.auto_restore |= args.auto_restore

    # In stdio mode stdout is the protocol channel: logs go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mcp = build(cfg)
    if args.http:
        from mcp.server.transport_security import TransportSecuritySettings

        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *args.allowed_host]
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=["https://chatgpt.com", "https://chat.openai.com", "http://127.0.0.1:*", "http://localhost:*"],
        )
        logging.getLogger(__name__).info("HTTP on http://%s:%s/mcp (allowed hosts: %s)", args.host, args.port, hosts)
        mcp.run("streamable-http", host=args.host, port=args.port, transport_security=security)
    else:
        mcp.run("stdio")
