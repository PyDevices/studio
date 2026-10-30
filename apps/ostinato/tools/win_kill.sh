#!/usr/bin/env bash
# Stop every desktop Ostinato (python.exe -m ostinato) and its engine
# processes on Windows: what a timed-out run leaves behind (killing cmd.exe
# from WSL doesn't stop the python.exe it started).
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*-m ostinato*' -or \$_.CommandLine -like '*ostinato-desktop*multiprocessing*' -or (\$_.CommandLine -like '*multiprocessing.spawn*' -and \$_.ExecutablePath -like '*gate315*') } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue; \$_.ProcessId }"
