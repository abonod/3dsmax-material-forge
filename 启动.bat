@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Material Forge
set "URL=http://127.0.0.1:8765"

echo ============================================
echo   Material Forge - 3ds Max / V-Ray 材质生成
echo ============================================
echo.

rem ---------- 1) 端口已在监听：不重复起服务，直接开页面 ----------
netstat -ano | findstr /c:"127.0.0.1:8765" | findstr /i "LISTENING" >nul
if not errorlevel 1 (
  echo [提示] 服务已在运行，直接打开页面。
  start "" "%URL%"
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

rem ---------- 2) 选一个真正可用的 python（排除 Windows 商店占位程序） ----------
set "PY="
for /d %%d in ("C:\Program Files\Tencent\Marvis\MarvisAgent\*") do if exist "%%d\runtime\python311\python.exe" set "PY=%%d\runtime\python311\python.exe"
for /d %%d in ("E:\Program Files\Tencent\Marvis\MarvisAgent\*") do if exist "%%d\runtime\python311\python.exe" set "PY=%%d\runtime\python311\python.exe"
for /d %%d in ("D:\Program Files\Tencent\Marvis\MarvisAgent\*") do if exist "%%d\runtime\python311\python.exe" set "PY=%%d\runtime\python311\python.exe"

if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i "WindowsApps" >nul || if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  echo [错误] 未找到可用的 Python。请安装 Python 3.9+ 或确认 Marvis 运行时目录存在。
  pause
  exit /b 1
)
echo [环境] Python: %PY%

rem ---------- 3) 依赖自检 ----------
"%PY%" -c "import fastapi, uvicorn, PIL, numpy, multipart" >nul 2>nul
if errorlevel 1 (
  echo [依赖] 缺少组件，开始安装...
  "%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络。
    pause
    exit /b 1
  )
)

rem ---------- 4) 起服务（独立窗口，关掉该窗口即停止服务） ----------
echo [启动] 正在拉起服务窗口...
start "Material Forge 服务" cmd /k ""%PY%" app.py"

rem ---------- 5) 探活成功后再打开浏览器，避免开到空白页 ----------
set /a n=0
:wait
ping -n 2 127.0.0.1 >nul
powershell -NoProfile -Command "try{ if((Invoke-WebRequest '%URL%/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){exit 0} else {exit 1} }catch{ exit 1 }" >nul 2>&1
if not errorlevel 1 goto ready
set /a n+=1
if %n% lss 40 goto wait
echo.
echo [错误] 等待 40 秒服务仍未就绪。请查看「Material Forge 服务」窗口里的报错信息。
pause
exit /b 1

:ready
echo [就绪] %URL%
start "" "%URL%"
echo.
echo 服务窗口已打开，关闭「Material Forge 服务」窗口即停止服务。
ping -n 5 127.0.0.1 >nul
exit /b 0
