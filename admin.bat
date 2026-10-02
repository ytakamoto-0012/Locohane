@echo off
set SELF_DIR=%~dp0
call "%SELF_DIR%python_env.bat"
cd %SELF_DIR%

rem Starts the settings dashboard (admin tool) and auto-starts the
rem "default" instance (Locohane itself, instances/default/).
rem Requires ADMIN_USERS to be set in the project-root .env (login
rem credentials for the admin tool); otherwise startup fails with an
rem error (see .env.example).
rem
rem The host/port this dashboard listens on come from config.ini's
rem [admin] section. Setting ADMIN_HOST/ADMIN_PORT environment variables
rem overrides them (see src/config.py).

python -m admin.server
pause
