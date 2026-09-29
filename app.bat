@echo off
set SELF_DIR=%~dp0
set PYTHON_DIR=C:\DT_Python\Python311\env_local_agent_system
set PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%
cd %SELF_DIR%

rem Uses the host/port from instances/default/instance.json so it stays in
rem sync with the admin tool (admin.bat) even if the port was changed there.
rem Falls back to 127.0.0.1:8000 if the file doesn't exist yet (first run,
rem or the admin tool has never been used).
set APP_HOST=127.0.0.1
set APP_PORT=8000
for /f "usebackq tokens=1,2" %%A in (`python -c "import json; from pathlib import Path; p = Path('instances/default/instance.json'); d = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(d.get('app_host', '127.0.0.1'), d.get('app_port', 8000))"`) do (
    set APP_HOST=%%A
    set APP_PORT=%%B
)

chainlit run app.py --host %APP_HOST% --port %APP_PORT%
pause
