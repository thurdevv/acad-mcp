"""Build the Claude Desktop extension (.mcpb, manifest 0.4, "uv" server type).

The uv type lets the host install the dependencies (pywin32, OpenCV and Pillow have compiled parts that
cannot be bundled portably). Usage:  python scripts/build_mcpb.py   (needs Node.js for npx)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "mcpb"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT / "src"))


async def _tools() -> tuple[list[dict], list[dict]]:
    from mcp import Client

    from acad_mcp.config import Config
    from acad_mcp.server import build

    async with Client(build(Config())) as c:
        tools = [{"name": t.name, "description": (t.description or "").split("\n")[0][:200]}
                 for t in (await c.list_tools()).tools]
        prompts = [{"name": p.name, "description": p.description or "",
                    "arguments": [a.name for a in (p.arguments or [])],
                    "text": "Use the recreate_drawing prompt of the acad server. ${arguments.notes}"}
                   for p in (await c.list_prompts()).prompts]
    return tools, prompts


def manifest(version: str, tools: list[dict], prompts: list[dict]) -> dict:
    return {
        "manifest_version": "0.4",
        "name": "acad-mcp",
        "display_name": "AutoCAD (acad-mcp)",
        "version": version,
        "description": "Let Claude draw, edit, read and check drawings in the AutoCAD running on your Windows PC.",
        "long_description": (
            "Drive a running AutoCAD (full version, Windows) from Claude: draw and edit, read large drawings fast, "
            "take-offs and block reports, layer standards, batch-process folders of DWGs without opening them, "
            "reusable LISP routines, import DXF/DWG/vector PDF and trace scanned images. Every write is one undo "
            "step. Not affiliated with Autodesk."
        ),
        "author": {"name": "thurdevv"},
        "license": "MIT",
        "icon": "icon.png",
        "keywords": ["autocad", "cad", "dwg", "dxf", "drafting", "engineering", "windows"],
        "server": {
            "type": "uv",
            "entry_point": "src/acad_mcp/__main__.py",
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", "acad-mcp"],
                "env": {
                    "ACAD_MCP_LANG": "${user_config.language}",
                    "ACAD_MCP_READ_ONLY": "${user_config.read_only}",
                    "ACAD_MCP_ALLOW_LISP": "${user_config.allow_lisp}",
                    "ACAD_MCP_AUTO_RESTORE": "${user_config.auto_restore}",
                },
            },
        },
        "compatibility": {"platforms": ["win32"], "runtimes": {"python": ">=3.13"}},
        "user_config": {
            "language": {"type": "string", "title": "Language / Idioma (en | pt)",
                         "description": "Language of tool descriptions and messages.", "default": "en"},
            "read_only": {"type": "boolean", "title": "Read-only",
                          "description": "Only reading tools (no drawing, editing or saving).", "default": False},
            "allow_lisp": {"type": "boolean", "title": "Allow AutoLISP",
                           "description": "Enable run_lisp, routines and batch (powerful; not a sandbox).",
                           "default": True},
            "auto_restore": {"type": "boolean", "title": "Restore minimized AutoCAD",
                             "description": "Restore the AutoCAD window when needed (it may grab focus).",
                             "default": False},
        },
        "tools": tools,
        "prompts": prompts,
    }


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tools, prompts = asyncio.run(_tools())
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, STAGE / name)
    shutil.copytree(ROOT / "src", STAGE / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "mcpb" / "icon.png", STAGE / "icon.png")
    (STAGE / "manifest.json").write_text(json.dumps(manifest(version, tools, prompts), indent=2), encoding="utf-8")
    (STAGE / ".mcpbignore").write_text(".venv/\n__pycache__/\n*.pyc\nserver/lib/\nserver/venv/\n", encoding="utf-8")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"acad-mcp-{version}.mcpb"
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        sys.exit("npx not found: install Node.js to pack the extension.")
    subprocess.run([npx, "-y", "@anthropic-ai/mcpb", "validate", str(STAGE / "manifest.json")], check=True)
    subprocess.run([npx, "-y", "@anthropic-ai/mcpb", "pack", str(STAGE), str(out)], check=True)
    print("extension:", out, f"({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
