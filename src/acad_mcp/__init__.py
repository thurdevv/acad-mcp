"""acad-mcp: MCP server that lets Claude (and other MCP clients) drive AutoCAD on Windows."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("acad-mcp")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"


def main() -> None:
    from .server import main as _main

    _main()
