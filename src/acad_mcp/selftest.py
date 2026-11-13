"""Installation self-test: build the server and list its tools (does not need AutoCAD running).

python -m acad_mcp.selftest   -> exit code 0 and a one-line summary when the install is good.
"""

from __future__ import annotations

import asyncio
import sys


async def _count() -> tuple[int, int]:
    from mcp import Client

    from .config import Config
    from .server import build

    async with Client(build(Config())) as c:
        return len((await c.list_tools()).tools), len((await c.list_prompts()).prompts)


def main() -> int:
    import logging

    logging.disable(logging.CRITICAL)
    try:
        tools, prompts = asyncio.run(_count())
    except Exception as exc:  # noqa: BLE001
        print(f"[x] server failed to load / servidor nao carregou: {exc}")
        return 1
    print(f"[ok] server loaded {tools} tools and {prompts} prompt(s) / servidor carregou {tools} ferramentas")
    return 0 if tools > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
