@echo off
chcp 936 >nul
title MONO 社区 v6.7 - 一键启动（环境配置+自检+启动 三合一）
cd /d "%~dp0"

echo ============================================================
echo   MONO 社区 v6.7  一键启动（环境配置 + 自检 + 启动 三合一）
echo ------------------------------------------------------------
echo   双击这一个就够了，它会自动依次完成：
echo     [1] 定位 Python（3.7/3.8/3.9/3.10+ 均可）
echo     [2] 修复 pip（ensurepip / get-pip.py，学校机常见 pip 损坏）
echo     [3] 安装依赖（flask 等固定版本组合，官方源失败转清华镜像）
echo     [4] 修复 sqlite3（精简版 Python 缺文件时自动下载补齐）
echo     [5] 检查关键文件 / 游戏引擎 / 数据库
echo     [6] 清理端口占用并启动服务
echo.
echo   !! 出问题时：把本窗口从上到下的全部输出复制/截图发出来，
echo   !! 每一行 [FAIL] / [WARN] 都直接指出问题所在，可远程定位。
echo ============================================================
echo.

rem ============================================================
rem  本脚本自身只做三件事：定位 Python / 文件预检 / 调用 start_server.py
rem  诊断、修复、启动全部在 start_server.py 里完成。
rem ============================================================

rem ---- 1. 定位 Python ----
rem  优先 py -3，其次 python；python 缺失时再兜底常见安装路径，
rem  避免"装了 Python 但没勾 Add to PATH"导致误报未安装。
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  rem 兜底：Start Menu 里的 py.exe 启动器（Windows 官方安装器一定会放）
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
  echo        （安装时勾选 Add Python to PATH；Win7 请用 3.7/3.8/3.9）
  echo        也可以用 python.exe 的完整路径运行：
  echo        "C:\完整路径\python.exe" start_server.py
  pause
  exit /b 1
)
echo [OK] 将使用 Python：%PY%
%PY% --version 2>&1
echo.

rem ---- 2. 本体文件预检（后续详细检查都在 start_server.py 里）----
for %%f in (app.py start_server.py admin_secret.py) do (
  if not exist "%%f" (
    echo [FAIL] 缺少文件 %%f，请确认整个 test6 文件夹已完整拷贝
    pause
    exit /b 1
  )
)
echo [OK] app.py / start_server.py / admin_secret.py 都在
echo.

rem ---- 3. 启动（pip 修复 / 依赖安装 / sqlite3 修复 / 引擎清单 / 端口清理全在里面）----
rem  PYTHONIOENCODING=:replace：保证任何代码页下打印中文都不会崩
rem  PYTHONUNBUFFERED=1：输出立刻落盘，异常退出时日志里能看到完整内容
set "PYTHONIOENCODING=:replace"
set "PYTHONUNBUFFERED=1"
%PY% start_server.py %*
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================
if not "%RC%"=="0" (
  echo   启动器异常退出（退出码 %RC%）。
  echo   请把本窗口从上到下的全部输出复制/截图发出来，可直接定位问题。
  echo   如果本窗口内容很短、看不到具体报错，请把同目录下的
  echo   server_run.log 也一起发出来（服务真正的报错在里面）。
) else (
  echo   服务已退出（正常关闭）。
  echo   若上面出现 [FAIL] 或 Traceback 报错，请把本窗口从上到下
  echo   的全部输出复制/截图发出来，可直接定位问题。
)
echo ============================================================
pause
