@echo off
rem Runs the tests. "live" tests open new test drawings in AutoCAD (open, NOT minimized) and close them unsaved.
rem Roda os testes. Os testes "live" abrem desenhos de teste no AutoCAD (aberto e NAO minimizado) e fecham sem salvar.
cd /d "%~dp0"
call "%~dp0scripts\ensure_uv.bat"
if errorlevel 1 (pause & exit /b 1)
"%UV%" run pytest -q -p no:cacheprovider %*
pause
