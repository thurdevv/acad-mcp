@echo off
rem Starts acad-mcp over HTTP (for ChatGPT via OpenAI Secure MCP Tunnel): http://127.0.0.1:8765/mcp
rem Inicia o acad-mcp em HTTP. Deixe esta janela aberta e o AutoCAD aberto, NAO minimizado.
cd /d "%~dp0"
call "%~dp0scripts\ensure_uv.bat"
if errorlevel 1 (pause & exit /b 1)
"%UV%" run acad-mcp --http --port 8765 %*
pause
