@echo off
set SELF_DIR=%~dp0
set PYTHON_DIR=C:\DT_Python\Python311\env_local_agent_system
set PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%
cd %SELF_DIR%
chainlit run app.py --host 127.0.0.1 --port 8000
pause
