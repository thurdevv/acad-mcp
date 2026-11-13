@echo off
setlocal EnableExtensions
title acad-mcp - instalador / installer
cd /d "%~dp0"
rem ACAD_MCP_INSTALL_DRYRUN=1 : rehearsal - does not close/open Claude nor write its config.

echo ============================================================
echo   acad-mcp  -  instalador completo / full installer
echo ============================================================
echo.

rem ---------- 1. AutoCAD ----------
reg query "HKCR\AutoCAD.Application" >nul 2>nul
if errorlevel 1 (
  echo [!] AutoCAD completo nao encontrado neste PC. O MCP precisa do AutoCAD completo ^(o LT nao serve^).
  echo [!] Full AutoCAD not found. The MCP needs full AutoCAD ^(LT does not work^). Continuing anyway...
  echo.
) else (
  echo [ok] AutoCAD encontrado / found
)

rem ---------- 2. Idioma / language ----------
set "LANGSEL="
set /p "LANGSEL=Idioma / Language [pt/en] (Enter = pt): "
if /i "%LANGSEL%"=="en" (set "LANGSEL=en") else (set "LANGSEL=pt")
echo [ok] idioma / language: %LANGSEL%
echo.

rem ---------- 3. Fechar o Claude / close Claude ----------
tasklist /FI "IMAGENAME eq claude.exe" /NH 2>nul | find /i "claude.exe" >nul
if errorlevel 1 goto :claude_closed
if "%ACAD_MCP_INSTALL_DRYRUN%"=="1" (
  echo [dry-run] Claude esta aberto: o instalador perguntaria para fechar.
  goto :claude_closed
)
echo O Claude esta aberto e precisa ser fechado para instalar (ele reabre no final).
echo Claude is running and must be closed to install (it is reopened at the end).
set "OKCLOSE="
set /p "OKCLOSE=Fechar o Claude agora? / Close Claude now? [S/N]: "
if /i not "%OKCLOSE%"=="S" if /i not "%OKCLOSE%"=="Y" (
  echo Instalacao cancelada. Feche o Claude e rode de novo. / Cancelled.
  goto :fail
)
taskkill /IM claude.exe /T >nul 2>nul
for /l %%i in (1,1,10) do (
  tasklist /FI "IMAGENAME eq claude.exe" /NH 2>nul | find /i "claude.exe" >nul || goto :claude_closed
  timeout /t 1 /nobreak >nul
)
taskkill /IM claude.exe /T /F >nul 2>nul
timeout /t 2 /nobreak >nul
:claude_closed
echo [ok] Claude fechado / closed
echo.

rem ---------- 4. uv ----------
call "%~dp0scripts\ensure_uv.bat"
if errorlevel 1 goto :fail

rem ---------- 5. Python + dependencias ----------
echo.
echo Instalando Python e dependencias (na 1a vez leva alguns minutos, precisa de internet)...
echo Installing Python and dependencies (first time takes a few minutes, needs internet)...
"%UV%" sync
if errorlevel 1 (
  echo [x] uv sync falhou - veja a mensagem acima. / uv sync failed - see above.
  goto :fail
)
echo [ok] dependencias instaladas / dependencies installed
echo.

rem ---------- 6. Configurar o Claude ----------
set "DRY="
if "%ACAD_MCP_INSTALL_DRYRUN%"=="1" set "DRY=--dry-run"
".venv\Scripts\python.exe" -m acad_mcp install-claude --lang %LANGSEL% --yes --no-reopen %DRY%
if errorlevel 1 goto :fail
echo.

rem ---------- 7. Autoteste / self-test ----------
".venv\Scripts\python.exe" -m acad_mcp.selftest
if errorlevel 1 goto :fail

rem ---------- 8. Reabrir o Claude / reopen Claude ----------
if "%ACAD_MCP_INSTALL_DRYRUN%"=="1" (
  for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "(Get-StartApps | Where-Object Name -eq 'Claude' | Select-Object -First 1).AppID"`) do echo [dry-run] reabriria / would reopen: shell:AppsFolder\%%a
  goto :done
)
for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "(Get-StartApps | Where-Object Name -eq 'Claude' | Select-Object -First 1).AppID"`) do set "CLAUDEAPP=%%a"
if defined CLAUDEAPP (
  start "" explorer.exe "shell:AppsFolder\%CLAUDEAPP%"
  echo [ok] Claude reaberto / reopened
) else (
  echo [!] Abra o Claude pelo menu Iniciar. / Open Claude from the Start menu.
)

:done
echo.
echo ============================================================
echo   PRONTO! Abra o AutoCAD, comece uma conversa NOVA no Claude
echo   e peca: "Use o MCP do AutoCAD: me diga o status".
echo   DONE! Open AutoCAD, start a NEW chat in Claude and ask:
echo   "Use the AutoCAD MCP: tell me the status".
echo ============================================================
pause
exit /b 0

:fail
echo.
echo Algo deu errado - tire uma foto desta janela. / Something failed - take a picture of this window.
pause
exit /b 1
