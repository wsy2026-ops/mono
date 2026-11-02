@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title 出口 IP 切换器

rem ==== Git 的 ssh 是 MSYS 程序，必须设 HOME 才能读到 ~/.ssh/config ====
if not defined HOME set "HOME=%USERPROFILE%"

set "LPORT=11080"
set "REGKEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
set "BACKUP=%TEMP%\mono-proxy-backup.txt"
set "PROFILE=%TEMP%\mono-proxy-browser"
set "TESTF=%TEMP%\mono-ssh-test.txt"
set "SSHOPT=-o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
set "SSHEXE="

:menu
cls
echo ============================================
echo    出口 IP 切换器
echo ============================================
echo.
echo  当前状态:
call :state
echo.
echo  [1] 切到香港服务器    出口 = 64.90.12.106
echo  [2] 切回本机          出口 = 你的本地宽带
echo  [3] 只开一个走香港的浏览器窗口（隔离，不动系统设置）
echo  [4] 测试当前出口 IP
echo  [5] 紧急恢复（清掉代理设置并断开隧道）
echo  [6] 诊断（出问题时选这个）
echo  [0] 退出
echo.
set "CH="
set /p "CH=请选择: "
if "%CH%"=="1" goto ON
if "%CH%"=="2" goto OFF
if "%CH%"=="3" goto ISOLATED
if "%CH%"=="4" goto TESTIP
if "%CH%"=="5" goto RESCUE
if "%CH%"=="6" goto DIAG
if "%CH%"=="0" goto QUIT
goto menu

rem ================= 工具子程序 =================
:istunnel
set "TUN=0"
netstat -ano | findstr ":%LPORT% " | findstr LISTENING >nul 2>&1
if errorlevel 1 exit /b 0
set "TUN=1"
exit /b 0

:getpx
set "PE="
for /f "tokens=3" %%a in ('reg query "%REGKEY%" /v ProxyEnable 2^>nul ^| findstr /i "ProxyEnable"') do set "PE=%%a"
exit /b 0

rem 查找 ssh：Git 版优先，因为它与你现在能用的完全一致；
rem Windows 自带 OpenSSH 对私钥权限检查很严，可能拒绝现有密钥。
:findssh
set "SSHEXE="
if exist "C:\Program Files\Git\usr\bin\ssh.exe" set "SSHEXE=C:\Program Files\Git\usr\bin\ssh.exe"
if defined SSHEXE exit /b 0
if exist "C:\Program Files (x86)\Git\usr\bin\ssh.exe" set "SSHEXE=C:\Program Files (x86)\Git\usr\bin\ssh.exe"
if defined SSHEXE exit /b 0
if exist "%LOCALAPPDATA%\Programs\Git\usr\bin\ssh.exe" set "SSHEXE=%LOCALAPPDATA%\Programs\Git\usr\bin\ssh.exe"
if defined SSHEXE exit /b 0
for /d %%d in ("%USERPROFILE%\.workbuddy-ai\binaries\PortableGit\versions\*") do if exist "%%d\usr\bin\ssh.exe" set "SSHEXE=%%d\usr\bin\ssh.exe"
if defined SSHEXE exit /b 0
if exist "C:\Windows\System32\OpenSSH\ssh.exe" set "SSHEXE=C:\Windows\System32\OpenSSH\ssh.exe"
if defined SSHEXE exit /b 0
for %%a in (ssh.exe) do set "SSHEXE=%%~$PATH:a"
exit /b 0

:refresh
if not exist "%~dp0_proxy_refresh.ps1" exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_proxy_refresh.ps1" >nul 2>&1
exit /b 0

:state
call :istunnel
if "%TUN%"=="1" goto st_run
echo    隧道     : 未运行
goto st_px
:st_run
echo    隧道     : 运行中
:st_px
call :getpx
if "%PE%"=="0x1" goto st_on
echo    全局代理 : 未开启
exit /b 0
:st_on
echo    全局代理 : 已开启
exit /b 0

rem ================= 建立隧道 =================
:up
call :istunnel
if "%TUN%"=="1" exit /b 0
call :findssh
if not defined SSHEXE goto UP_NOSSHEXE
set "KEYF=%USERPROFILE%\.ssh\id_ed25519_vps"
set "KHF=%USERPROFILE%\.ssh\known_hosts"
set "USEEXPLICIT="
echo.
echo  正在检测 SSH 连接...
"%SSHEXE%" %SSHOPT% mono-vps "echo MONO_SSH_OK" >"%TESTF%" 2>&1
findstr /c:"MONO_SSH_OK" "%TESTF%" >nul 2>&1
if not errorlevel 1 goto UP_GO
rem 别名方式失败时回退：不依赖 ~/.ssh/config，直接指定密钥与主机
echo  按别名连接失败，改用显式参数重试...
if not exist "%KEYF%" goto UP_FAIL
"%SSHEXE%" -i "%KEYF%" -o UserKnownHostsFile="%KHF%" %SSHOPT% root@64.90.12.106 "echo MONO_SSH_OK" >"%TESTF%" 2>&1
findstr /c:"MONO_SSH_OK" "%TESTF%" >nul 2>&1
if errorlevel 1 goto UP_FAIL
set "USEEXPLICIT=1"
:UP_GO
echo  连接正常，正在建立隧道...
if defined USEEXPLICIT goto UP_GO2
start "出口IP跳板-用完再关" /min "%SSHEXE%" %SSHOPT% -D %LPORT% -N mono-vps
goto UP_WAIT
:UP_GO2
start "出口IP跳板-用完再关" /min "%SSHEXE%" -i "%KEYF%" -o UserKnownHostsFile="%KHF%" %SSHOPT% -D %LPORT% -N root@64.90.12.106
:UP_WAIT
timeout /t 6 /nobreak >nul
call :istunnel
if "%TUN%"=="1" exit /b 0
echo.
echo  [!] SSH 能连通，但隧道没监听端口 %LPORT%。
echo      可能端口被占用。可编辑本文件开头的 LPORT 换成别的值。
exit /b 1

:UP_NOSSHEXE
echo.
echo  [!] 找不到 ssh 程序。
echo      请确认已安装 Git for Windows，或在 Windows 设置里启用 OpenSSH 客户端。
exit /b 1

:UP_FAIL
echo.
echo  [!] SSH 连接失败。原始报错如下:
echo  --------------------------------------------------
type "%TESTF%"
echo  --------------------------------------------------
echo.
echo  常见原因与处理:
echo   * Permissions for ... are too open
echo     私钥权限过宽。在 cmd 里执行这行修复:
echo     icacls "%USERPROFILE%\.ssh\id_ed25519_vps" /inheritance:r /grant:r "%USERNAME%:R"
echo   * Permission denied publickey
echo     密钥未生效，检查 SSH 配置里的 IdentityFile 路径
echo   * Could not resolve hostname mono-vps
echo     没读到 SSH 配置，确认 %USERPROFILE%\.ssh\config 里有 mono-vps 段
echo   * 长时间无输出 / Connection timed out
echo     本机到服务器的网络不通
exit /b 1

rem ================= 备份 / 还原系统代理 =================
:backup
if exist "%BACKUP%" exit /b 0
set "PE0=0"
set "PS0="
for /f "tokens=3" %%a in ('reg query "%REGKEY%" /v ProxyEnable 2^>nul ^| findstr /i "ProxyEnable"') do set "PE0=%%a"
for /f "tokens=2*" %%a in ('reg query "%REGKEY%" /v ProxyServer 2^>nul ^| findstr /i "ProxyServer"') do set "PS0=%%b"
>"%BACKUP%" echo %PE0%
>>"%BACKUP%" echo %PS0%
echo   已备份你原有的代理设置（切回时会自动还原）
exit /b 0

:loadbackup
set "PE0=0"
set "PS0="
if not exist "%BACKUP%" exit /b 0
set /p PE0=<"%BACKUP%"
for /f "skip=1 delims=" %%a in ('type "%BACKUP%"') do set "PS0=%%a"
exit /b 0

rem ================= [1] 切到香港 =================
:ON
call :up
if errorlevel 1 goto PAUSE_MENU
call :backup
reg add "%REGKEY%" /v ProxyEnable /t REG_DWORD /d 1 /f >nul
reg add "%REGKEY%" /v ProxyServer /t REG_SZ /d "socks=127.0.0.1:%LPORT%" /f >nul
call :refresh
echo.
echo  已切到香港服务器，出口 IP 现在是 64.90.12.106
echo  提示: 若浏览器未立即生效，重启一下浏览器即可。
goto PAUSE_MENU

rem ================= [2] 切回本机 =================
:OFF
echo.
echo  正在切回本机...
reg add "%REGKEY%" /v ProxyEnable /t REG_DWORD /d 0 /f >nul
call :loadbackup
if not "%PS0%"=="" reg add "%REGKEY%" /v ProxyServer /t REG_SZ /d "%PS0%" /f >nul
if not "%PE0%"=="" reg add "%REGKEY%" /v ProxyEnable /t REG_DWORD /d %PE0% /f >nul
call :refresh
call :istunnel
if "%TUN%"=="0" goto OFF_DONE
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%LPORT% " ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1
:OFF_DONE
echo  已切回本机，出口 IP 恢复为你的本地宽带。
goto PAUSE_MENU

rem ================= [3] 隔离浏览器 =================
:ISOLATED
call :up
if errorlevel 1 goto PAUSE_MENU
set "BR="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "BR=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined BR if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" set "BR=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not defined BR if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" set "BR=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
if not defined BR goto NO_BR
start "" "%BR%" --proxy-server="socks5://127.0.0.1:%LPORT%" --user-data-dir="%PROFILE%" "https://ipv4.icanhazip.com"
echo.
echo  已打开一个独立浏览器窗口（走香港出口）。
echo  它用独立的登录状态，不影响你日常浏览器的设置。
goto PAUSE_MENU

:NO_BR
echo.
echo  没找到 Chrome 或 Edge。
goto PAUSE_MENU

rem ================= [4] 测试出口 IP =================
rem 注意：不用 api.ipify.org —— 它在国内不通；也不用 ifconfig.me —— 它会返回 IPv6。
:TESTIP
echo.
echo  直连出口（本机）:
set "IP1="
for /f "delims=" %%a in ('curl -s --max-time 10 --noproxy "*" https://ipv4.icanhazip.com 2^>nul') do set "IP1=%%a"
if not defined IP1 for /f "delims=" %%a in ('curl -s --max-time 10 --noproxy "*" https://ipinfo.io/ip 2^>nul') do set "IP1=%%a"
if not defined IP1 set "IP1=(查询失败，可能是网络问题)"
echo    %IP1%
echo.
call :istunnel
if "%TUN%"=="0" goto TI_NOTUN
echo  经香港出口:
set "IP2="
for /f "delims=" %%a in ('curl -s --max-time 10 --socks5-hostname 127.0.0.1:%LPORT% https://ipv4.icanhazip.com 2^>nul') do set "IP2=%%a"
if not defined IP2 for /f "delims=" %%a in ('curl -s --max-time 10 --socks5-hostname 127.0.0.1:%LPORT% https://ipinfo.io/ip 2^>nul') do set "IP2=%%a"
if not defined IP2 set "IP2=(查询失败)"
echo    %IP2%
echo.
echo  说明: 经香港出口应显示 64.90.12.106
goto PAUSE_MENU
:TI_NOTUN
echo  经香港出口: 隧道未运行（先选 1 或 3）
goto PAUSE_MENU

rem ================= [5] 紧急恢复 =================
:RESCUE
echo.
echo  正在清除代理设置并断开隧道...
reg add "%REGKEY%" /v ProxyEnable /t REG_DWORD /d 0 /f >nul
reg delete "%REGKEY%" /v ProxyServer /f >nul 2>&1
call :refresh
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%LPORT% " ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1
if exist "%BACKUP%" del /q "%BACKUP%" >nul 2>&1
echo  已恢复。浏览器现在走本机直连。
goto PAUSE_MENU

rem ================= [6] 诊断 =================
:DIAG
echo.
echo  ============== 诊断信息 ==============
echo  [环境]
echo    USERPROFILE = %USERPROFILE%
echo    HOME        = %HOME%
echo.
echo  [ssh]
call :findssh
if defined SSHEXE goto DG_S1
echo    ssh 路径    = 未找到
goto DG_S2
:DG_S1
echo    ssh 路径    = %SSHEXE%
:DG_S2
echo    SSH 配置    = %USERPROFILE%\.ssh\config
if exist "%USERPROFILE%\.ssh\config" goto DG_C1
echo    配置文件    = 不存在
goto DG_C2
:DG_C1
echo    配置文件    = 存在
:DG_C2
if exist "%USERPROFILE%\.ssh\id_ed25519_vps" goto DG_K1
echo    私钥文件    = 不存在
goto DG_K2
:DG_K1
echo    私钥文件    = 存在
:DG_K2
echo.
echo  [隧道]
call :istunnel
if "%TUN%"=="1" goto DG_T1
echo    端口 %LPORT%  = 未监听
goto DG_T2
:DG_T1
echo    端口 %LPORT%  = 监听中
:DG_T2
echo.
echo  [系统代理]
call :getpx
echo    ProxyEnable = %PE%
echo    ProxyServer =
reg query "%REGKEY%" /v ProxyServer 2>nul | findstr /i "ProxyServer"
echo.
echo  [SSH 连通性]
if not defined SSHEXE goto DG_NOSSH
"%SSHEXE%" %SSHOPT% mono-vps "echo MONO_SSH_OK" >"%TESTF%" 2>&1
type "%TESTF%"
echo.
echo  =====================================
goto PAUSE_MENU
:DG_NOSSH
echo    跳过（未找到 ssh）
goto PAUSE_MENU

:PAUSE_MENU
echo.
pause
goto menu

:QUIT
endlocal
exit /b 0
