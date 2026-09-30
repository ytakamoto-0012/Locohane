@echo off
rem stdio launcher for the Locohane skills MCP server (mcp_server/server.py).
rem Must not print anything to stdout: stdout is the MCP JSON-RPC channel.
call "%~dp0python_env.bat"
"%LOCOHANE_PYTHON%" "%~dp0mcp_server\server.py"
