@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python313\python.exe" set "PYEXE=%ProgramFiles%\Python313\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python312\python.exe" set "PYEXE=%ProgramFiles%\Python312\python.exe"
if not defined PYEXE for %%P in (python.exe) do if not defined PYEXE if exist "%%~$PATH:P" set "PYEXE=%%~$PATH:P"
if not defined PYEXE (
  echo 没有找到 python，请先安装 Python 3.8 以上版本，安装时勾选 Add Python to PATH。
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 if not "%NEUROLINK_NO_ELEVATE%"=="1" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if exist "%PYWEXE%" (
  start "NEUROLINK" "%PYWEXE%" app.py
  exit /b
)
"%PYEXE%" app.py
if errorlevel 1 pause