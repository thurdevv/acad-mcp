@echo off
rem Finds uv (installs it if missing) and leaves its full path in %UV%.
rem Also removes a .venv copied from another PC (it points to a Python that does not exist here).
rem Encontra o uv (instala se faltar) e deixa o caminho em %UV%. Remove .venv copiado de outro PC.

set "UV="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV set "UV=%%i"
if not defined UV call :search
if defined UV goto :venv

echo.
echo [acad-mcp] uv not found: installing it... / uv nao encontrado: instalando...
where winget >nul 2>nul && winget install --id astral-sh.uv -e --silent --accept-source-agreements --accept-package-agreements
call :search
if defined UV goto :venv

echo [acad-mcp] winget did not work, trying the official uv installer (astral.sh)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
call :search
if defined UV goto :venv

echo.
echo [acad-mcp] ERROR: could not install uv. Install it by hand: https://docs.astral.sh/uv/getting-started/installation/
echo [acad-mcp] ERRO: nao consegui instalar o uv. Instale manualmente pelo link acima e rode de novo.
exit /b 1

:venv
echo [acad-mcp] uv: %UV%
if exist "%~dp0..\.venv\pyvenv.cfg" (
  "%~dp0..\.venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo [acad-mcp] .venv was copied from another PC: recreating it / .venv copiado de outro PC: recriando
    rmdir /s /q "%~dp0..\.venv"
  )
)
exit /b 0

:search
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe" & exit /b 0
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" & exit /b 0
for /d %%d in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*") do if exist "%%d\uv.exe" set "UV=%%d\uv.exe"
if defined UV exit /b 0
if exist "%CARGO_HOME%\bin\uv.exe" set "UV=%CARGO_HOME%\bin\uv.exe"
exit /b 0
