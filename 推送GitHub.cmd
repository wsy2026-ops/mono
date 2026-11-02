@echo off
chcp 936 >nul
title MONO 社区 - 推送 GitHub
cd /d "%~dp0"

echo ============================================================
echo   第一次推送 GitHub（三步）
echo ------------------------------------------------------------
echo   第 1 步（你做）：网页上建一个空仓库
echo     1. 打开 https://github.com/new
echo     2. Repository name 随便起，如 test6
echo     3. 选 Private（私有）！！！
echo     4. 不要勾选 Add a README / .gitignore / license
echo     5. 点 Create repository，然后复制页面给出的仓库地址
echo        形如 https://github.com/你的用户名/test6.git
echo.
echo   第 2 步：把地址粘贴到下面（回车）
echo   第 3 步：弹出浏览器登录 GitHub 授权一次即可
echo ============================================================
echo.

set /p REPO=请粘贴仓库地址（https://github.com/用户名/仓库名.git）: 
if "%REPO%"=="" (
  echo [FAIL] 未输入地址，已取消
  pause
  exit /b 1
)

git remote remove origin >nul 2>&1
git remote add origin "%REPO%"
git config http.postBuffer 524288000
echo.
echo [..] 开始推送（仓库约 420MB，第一次要等几分钟，别关窗口）...
git push -u origin main
if errorlevel 1 (
  echo.
  echo [FAIL] 推送失败。常见原因：
  echo   - 仓库建成了 Public/已勾选 README  → 删掉重建空仓库
  echo   - 浏览器授权没完成                 → 重新运行本脚本
  echo   - 网络问题                         → 挂代理后重试
  pause
  exit /b 1
)
echo.
echo [OK] 推送成功！以后每次改完代码，推送只需一条命令：
echo      git push
echo      （学校电脑第一次用：git pull 即可拉到最新代码）
pause
