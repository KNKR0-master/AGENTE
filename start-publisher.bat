@echo off
setlocal
set "PROJECT_ROOT=%~dp0"

if exist "%PROJECT_ROOT%.venv\Scripts\python.exe" goto run_venv

where python >nul 2>&1
if not errorlevel 1 goto run_path_python

set "PYTHON_EXE="
for %%V in (314 313 312 311 310) do if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
if defined PYTHON_EXE goto run_found_python

if exist "%SystemRoot%\py.exe" goto run_py

echo Python nao encontrado. Instale Python ou configure um ambiente .venv no projeto.
set "EXIT_CODE=1"
goto finish

:run_venv
"%PROJECT_ROOT%.venv\Scripts\python.exe" "%PROJECT_ROOT%scripts\start_publisher.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:run_path_python
python "%PROJECT_ROOT%scripts\start_publisher.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:run_found_python
"%PYTHON_EXE%" "%PROJECT_ROOT%scripts\start_publisher.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:run_py
"%SystemRoot%\py.exe" -3 "%PROJECT_ROOT%scripts\start_publisher.py"
set "EXIT_CODE=%ERRORLEVEL%"

:finish
endlocal & exit /b %EXIT_CODE%
