"""Add the 'acad' server to Claude Desktop's config (any Windows PC).

Run it with Claude FULLY CLOSED: if the app is open, it rewrites the file from
memory when it quits and the new entry is lost (observed during development).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EXE = PROJECT / ".venv" / "Scripts" / "acad-mcp.exe"
SERVER_NAME = "acad"
OLD_NAMES = ("autocad",)  # entries from earlier versions, replaced


def config_candidates() -> list[Path]:
    """Every place Claude Desktop keeps claude_desktop_config.json on Windows (classic and Store/MSIX)."""
    local, roaming = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
    pats = [
        os.path.join(roaming, "Claude", "claude_desktop_config.json"),
        os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"),
        os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"),
    ]
    found: list[Path] = []
    for p in pats:
        found += [Path(x) for x in glob.glob(p)]
    return found


def claude_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"], capture_output=True, text=True).stdout
    return "claude.exe" in out.lower()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "pt"], default=None)
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = ap.parse_args()
    if not EXE.is_file():
        print(f"ERROR: {EXE} not found. Run 'uv sync' in the project folder first.")
        return 1
    if args.dry_run:
        entry = {"command": str(EXE), "args": [], **({"env": {"ACAD_MCP_LANG": args.lang}} if args.lang else {})}
        for p in config_candidates() or [Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"]:
            print(f"[dry-run] would write mcpServers.{SERVER_NAME} = {json.dumps(entry)}")
            print(f"[dry-run]   into {p}")
        return 0
    if claude_running():
        print("Claude is still running. Quit it COMPLETELY (tray icon near the clock > right click > Quit)")
        print("and run this again.  /  O Claude ainda está aberto: feche-o por completo e rode de novo.")
        return 2
    targets = config_candidates()
    if not targets:
        default = Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text("{}", encoding="utf-8")
        targets = [default]
    for cfg_path in targets:
        raw = cfg_path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        cfg = json.loads(raw.decode("utf-8-sig") or "{}")
        backup = cfg_path.with_name(cfg_path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(cfg_path, backup)
        servers = cfg.setdefault("mcpServers", {})
        for old in OLD_NAMES:
            servers.pop(old, None)
        entry: dict = {"command": str(EXE), "args": []}
        if args.lang:
            entry["env"] = {"ACAD_MCP_LANG": args.lang}
        servers[SERVER_NAME] = entry
        data = json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8")
        cfg_path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
        check = json.loads(cfg_path.read_bytes().decode("utf-8-sig"))
        ok = SERVER_NAME in check.get("mcpServers", {})
        print(f"{'OK' if ok else 'FAILED'}: {cfg_path}")
        print(f"    servers: {list(check.get('mcpServers', {}))}   (backup: {backup.name})")
    print()
    print("Done. Open Claude and start a NEW conversation.  /  Pronto: abra o Claude numa conversa nova.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
