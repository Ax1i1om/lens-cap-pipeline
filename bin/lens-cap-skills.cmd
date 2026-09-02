@echo off
rem SPDX-License-Identifier: Apache-2.0
rem Windows companion for the repository-local Skill synchroniser.
setlocal
set "ROOT=%~dp0.."
rem Prefer the project environment when present; the synchroniser itself is
rem dependency-free, but this keeps all repository launchers consistent.
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\install_skills.py" %*
) else (
  py -3 "%ROOT%\scripts\install_skills.py" %*
)
exit /b %ERRORLEVEL%
