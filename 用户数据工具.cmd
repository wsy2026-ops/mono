@echo off
chcp 936 >nul
title MONO 社区 - 用户数据 打包/恢复
cd /d "%~dp0"

echo ============================================================
echo   MONO 社区  用户数据 打包 / 恢复工具
echo   只动数据（data.db / 密钥 / uploads / 头像），不动代码
echo   打包前请先关闭正在运行的服务！
echo ============================================================
echo.

rem ---- 定位 Python：py -3 -^> python -^> 常见安装目录兜底 ----
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  for %%d in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%\Python*" "%ProgramFiles(x86)%\Python*") do (
    if not defined PY if exist "%%~d\py.exe" set "PY=%%~d\py.exe -3"
  )
)
if not defined PY (
  for %%d in ("%LOCALAPPDATA%\Programs\Python\Python*" "%ProgramFiles%\Python*" "%ProgramFiles(x86)%\Python*") do (
    if not defined PY if exist "%%~d\python.exe" set "PY=%%~d\python.exe"
  )
)
if not defined PY (
  echo [FAIL] 未找到 Python，请先安装 Python 3.7 及以上版本
  echo        （安装时勾选 Add Python to PATH）
  pause
  exit /b 1
)
echo [OK] 将使用 Python：%PY%
echo.

set "PYTHONIOENCODING=:replace"
set "PYTHONUNBUFFERED=1"
%PY% user_data_tool.py
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [FAIL] 工具异常退出（退出码 %RC%），请把上面的完整输出发出来。
pause
