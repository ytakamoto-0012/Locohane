@echo off
set SELF_DIR=%~dp0
set PYTHON_DIR=C:\DT_Python\Python311\env_local_agent_system
set PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%
cd %SELF_DIR%

rem Starts the settings dashboard (admin tool). Defaults to the [admin]
rem section in config.ini (default http://127.0.0.1:8001) and auto-starts
rem the "default" instance (Locohane itself, instances/default/).
rem Requires ADMIN_USERS to be set in the project-root .env (login
rem credentials for the admin tool); otherwise startup fails with an
rem error (see .env.example).
rem
rem Optional arguments override host/port for this run only (take
rem priority over config.ini's [admin] section via the ADMIN_HOST/
rem ADMIN_PORT env vars; see src/config.py):
rem   admin.bat                  use the configured host/port
rem   admin.bat 8080             override port only (a pure-digit arg is
rem                              treated as the port)
rem   admin.bat 0.0.0.0          override host only
rem   admin.bat 0.0.0.0 8080     override both (either order works)

set ADMIN_HOST=127.0.0.1
set ADMIN_PORT=7999

python -m admin.server
pause
