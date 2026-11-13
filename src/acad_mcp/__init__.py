"""MCP para controlar o AutoCAD pelo Claude ou ChatGPT."""


def main() -> None:
    from .server import main as _main

    _main()
