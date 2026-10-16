@echo off
rem ============================================================
rem  Push this folder to your GitHub repo.
rem
rem  Usage:  push_to_github.bat
rem          push_to_github.bat https://github.com/USER/REPO.git
rem
rem  Why does it need a proxy?  On many China networks github.com is
rem  blocked, and git does NOT use the Windows system proxy
rem  automatically - it has to be told. This script tries the local
rem  proxy first, then a direct connection.
rem
rem  NOTE: kept ASCII-only on purpose.
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

rem -------------------------------------------------------------
rem 1. Pick a git that ships with the credential manager, so the
rem    GitHub sign-in window can pop up.  D:\Git is the system one.
rem -------------------------------------------------------------
set "GITEXE="
if exist "D:\Git\cmd\git.exe" set "GITEXE=D:\Git\cmd\git.exe"
if not defined GITEXE for %%P in (git.exe) do if not defined GITEXE (where %%P >nul 2>nul && set "GITEXE=git.exe")
if not defined GITEXE (
    echo [!] git not found. Install "Git for Windows" first.
    pause
    exit /b 1
)
echo Using git : %GITEXE%

rem -------------------------------------------------------------
rem 2. Repo URL
rem -------------------------------------------------------------
set "REPO=%~1"
if "%REPO%"=="" set /p REPO=Repo URL (https://github.com/USER/REPO.git): 
if "%REPO%"=="" (
    echo [!] No URL given. Aborting.
    pause
    exit /b 1
)
echo Repo      : %REPO%

rem -------------------------------------------------------------
rem 3. Point origin at it
rem -------------------------------------------------------------
"%GITEXE%" remote remove origin >nul 2>nul
"%GITEXE%" remote add origin "%REPO%"
"%GITEXE%" branch -M main

rem -------------------------------------------------------------
rem 4. Push - try the local proxy first, then direct.
rem    Port 7890 is FlClash / Clash default. Override by setting
rem    ACGNX_PROXY before running, e.g.  set ACGNX_PROXY=http://127.0.0.1:7897
rem -------------------------------------------------------------
set "PROXY=%ACGNX_PROXY%"
if "%PROXY%"=="" set "PROXY=http://127.0.0.1:7890"

echo.
echo [1/2] Pushing through local proxy %PROXY% ...
"%GITEXE%" -c http.proxy=%PROXY% -c https.proxy=%PROXY% push -u origin main
if not errorlevel 1 goto :ok

echo.
echo [2/2] Proxy attempt failed. Trying a direct connection ...
"%GITEXE%" push -u origin main
if not errorlevel 1 goto :ok

echo.
echo ------------------------------------------------------------
echo [!] Both attempts failed. Checklist:
echo.
echo     1) Is your proxy actually running?
echo        Right now this script assumed: %PROXY%
echo        If your proxy uses another port, run:
echo            set ACGNX_PROXY=http://127.0.0.1:YOUR-PORT
echo        then run this file again.
echo.
echo     2) Did a GitHub sign-in window appear?
echo        Sign in, then run this file again.
echo.
echo     3) Does the repo really exist and is it EMPTY?
echo        The URL should look like:
echo            https://github.com/YOUR-USERNAME/acgnx-watcher.git
echo        Note: it is your USERNAME, not your display name.
echo ------------------------------------------------------------
pause
exit /b 1

:ok
echo.
echo ============================================================
echo  [OK] Pushed to GitHub.
echo ============================================================
echo.
echo  Now add the Secret (this is what makes the bot able to
echo  message you):
echo.
echo    Repo -^> Settings -^> Secrets and variables -^> Actions
echo         -^> New repository secret
echo.
echo    Name  : ACGNX_WEBHOOK_URL
echo    Value : your Feishu bot webhook URL
echo.
echo  Then check the Actions tab - it runs every 5 minutes.
echo.
pause
