@echo off
chcp 936 >nul
setlocal
set LPORT=11080
set PROFILE=%TEMP%\mono-cf-proxy

echo ============================================
echo   Cloudflare 跳板启动器
echo ============================================
echo.

echo [1/4] 清理旧隧道...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%LPORT% " ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1

echo [2/4] 建立香港跳板隧道...
start "CF跳板-用完再关" /min ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -D %LPORT% -N mono-vps

echo [3/4] 等待隧道就绪（约 4 秒）...
timeout /t 4 /nobreak >nul

echo [4/4] 打开 Cloudflare 后台...
set BROWSER=
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
set EDGE1=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set EDGE2=C:\Program Files\Microsoft\Edge\Application\msedge.exe
if exist "%CHROME%" set BROWSER=%CHROME%
if not defined BROWSER if exist "%EDGE1%" set BROWSER=%EDGE1%
if not defined BROWSER if exist "%EDGE2%" set BROWSER=%EDGE2%
if not defined BROWSER goto NOBROWSER
start "" "%BROWSER%" --proxy-server="socks5://127.0.0.1:%LPORT%" --user-data-dir="%PROFILE%" "https://dash.cloudflare.com"
goto DONE

:NOBROWSER
echo   ！没找到 Chrome / Edge。
echo   请手动把浏览器代理设为 SOCKS5  127.0.0.1:%LPORT%
pause
exit /b 1

:DONE
echo.
echo ============================================
echo  完成！浏览器已通过香港服务器访问 Cloudflare。
echo.
echo  注意：那个最小化的「CF跳板-用完再关」窗口
echo        千万别关，关了隧道就断，浏览器会打不开。
echo.
echo  用完 Cloudflare 后，直接关掉那个窗口即可。
echo ============================================
echo.
pause
