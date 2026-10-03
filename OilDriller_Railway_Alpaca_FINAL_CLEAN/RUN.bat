@echo off
echo === OilDriller Railway - RUN - UCO ONLY ===
set PYTHON_EXE=
where py >nul 2>&1
if %errorlevel%==0 ( set PYTHON_EXE=py -3 & goto :found )
where python >nul 2>&1
if %errorlevel%==0 ( set PYTHON_EXE=python & goto :found )
set PYTHON_EXE=C:\Users\%USERNAME%\AppData\Local\Python\pythoncore-3.14-64\python.exe
:found
echo Using: %PYTHON_EXE%
%PYTHON_EXE% -m pip install --quiet fastapi uvicorn requests apscheduler
%PYTHON_EXE% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
