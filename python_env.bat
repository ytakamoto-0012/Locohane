@echo off
rem Single source of truth for the base Python environment of this project.
rem Edit PYTHON_DIR below only; app.bat / admin.bat / mcp_server.bat call this
rem file, and child processes (instances, run_script, skill scripts) inherit
rem it via sys.executable or the LOCOHANE_PYTHON environment variable.
set PYTHON_DIR=C:\DT_Python\Python311\env_local_agent_system
set LOCOHANE_PYTHON=%PYTHON_DIR%\Scripts\python.exe
set PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%
