@echo off
rem SPDX-License-Identifier: Apache-2.0
rem Windows companion for the repository-local native 3MF bridge.
setlocal
set "ROOT=%~dp0.."
rem Prefer the project environment created by scripts\bootstrap.py.  The
rem `py -3` launcher otherwise bypasses an activated venv on some Windows
rem installations and may not see NumPy/Pillow.
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\build_3mf.py" %*
) else (
  py -3 "%ROOT%\scripts\build_3mf.py" %*
)
exit /b %ERRORLEVEL%
