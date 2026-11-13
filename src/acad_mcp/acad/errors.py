"""Errors with messages meant to be read and acted upon by the AI model."""

from mcp.server.mcpserver.exceptions import ToolError


class AcadError(ToolError):
    """Expected error (AutoCAD closed, no drawing, entity not found...)."""
