@echo off
rem rhm - Remote Harness MCP console menu (Windows launcher).
setlocal
cd /d "%~dp0.."
if defined MESH_PYTHON (
    "%MESH_PYTHON%" -m core.menu %*
    exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 -m core.menu %*
    exit /b %ERRORLEVEL%
)
python -m core.menu %*
exit /b %ERRORLEVEL%
