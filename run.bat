@echo off
cd /d %~dp0
set PYTHON_EXE=E:\anaconda3\python.exe
set PID_FILE=%~dp0bot.pid

:: ====== GitHub 源及镜像 ======
set GIT_ORIGIN=https://github.com/YUASDS/official_bot.git
set GIT_MIRROR1=https://gitclone.com/github.com/YUASDS/official_bot.git
set GIT_MIRROR2=https://gh-proxy.com/https://github.com/YUASDS/official_bot.git

git config --global http.sslVerify false


:start
call :try_fetch
if %errorlevel% equ 0 (
    goto :check_behind
)
echo [%date% %time%] All sources unreachable, skipping update check.
goto :runbot

:try_fetch
echo [%date% %time%] Trying direct...
git remote set-url origin "%GIT_ORIGIN%" 2>nul
git -c http.timeout=15 fetch 2>nul
if %errorlevel% equ 0 ( exit /b 0 )

echo [%date% %time%] Trying mirror1 (gitclone)...
git remote set-url origin "%GIT_MIRROR1%" 2>nul
git -c http.timeout=15 fetch 2>nul
if %errorlevel% equ 0 ( exit /b 0 )

echo [%date% %time%] Trying mirror2 (gh-proxy)...
git remote set-url origin "%GIT_MIRROR2%" 2>nul
git -c http.timeout=15 fetch 2>nul
if %errorlevel% equ 0 ( exit /b 0 )

exit /b 1

:check_behind
for /f %%i in ('git rev-list HEAD...@{u} --count 2^>nul') do set BEHIND=%%i
if not defined BEHIND set BEHIND=0

if %BEHIND% GTR 0 (
    echo [%date% %time%] Update found! Pulling changes...
    if exist "%PID_FILE%" (
        call :killbot
    )
    git -c http.timeout=30 pull 2>nul
    echo [%date% %time%] Update complete, restarting...
    goto start
)

:runbot
echo [%date% %time%] Starting bot...
start "official_bot" "%PYTHON_EXE%" main.py
timeout /t 2 /nobreak >nul

for /f "tokens=2" %%a in ('tasklist /fi "WINDOWTITLE eq official_bot" /fo list 2^>nul ^| findstr "PID:"') do (
    echo %%a > "%PID_FILE%"
)

:wait_loop
timeout /t 60 /nobreak >nul

tasklist /fi "WINDOWTITLE eq official_bot" 2>nul | find "python.exe" >nul
if errorlevel 1 (
    echo [%date% %time%] Bot has stopped, restarting...
    goto start
)

call :try_fetch
if %errorlevel% neq 0 (
    echo [%date% %time%] All sources unreachable, will retry next round.
    goto wait_loop
)

for /f %%i in ('git rev-list HEAD...@{u} --count 2^>nul') do set BEHIND=%%i
if not defined BEHIND set BEHIND=0

if %BEHIND% GTR 0 (
    echo [%date% %time%] Update found! Stopping bot...
    call :killbot
    git -c http.timeout=30 pull 2>nul
    echo [%date% %time%] Update complete, restarting...
    goto start
)

goto wait_loop

:killbot
echo [%date% %time%] Killing bot process...
taskkill /fi "WINDOWTITLE eq official_bot" /f >nul 2>nul
if exist "%PID_FILE%" (
    set /p BOT_PID=<"%PID_FILE%"
    taskkill /PID %BOT_PID% /f >nul 2>nul
    del "%PID_FILE%" >nul 2>nul
)
timeout /t 2 /nobreak >nul
goto :eof
