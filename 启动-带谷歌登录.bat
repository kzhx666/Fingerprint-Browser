@echo off
chcp 65001 >nul
REM KZ Browser launcher with Google OAuth credentials.
REM Credentials are read from D:\KZProfiles\google-oauth.json at runtime.
REM Never put real API keys or client secrets into this file.
setlocal
set "OAUTH_JSON=D:\KZProfiles\google-oauth.json"
set "CHROME_EXE=D:\KZBuild\App\chrome.exe"
set "PROFILE_DIR=D:\KZProfiles\ExampleInstance\Data"
if not exist "%OAUTH_JSON%" (
  echo Missing %OAUTH_JSON% - copy google-oauth.example.json and fill it in.
  exit /b 1
)
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command ^
  "(Get-Content '%OAUTH_JSON%' -Raw | ConvertFrom-Json).api_key"`) do set "GOOGLE_API_KEY=%%A"
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command ^
  "(Get-Content '%OAUTH_JSON%' -Raw | ConvertFrom-Json).client_id"`) do set "GOOGLE_DEFAULT_CLIENT_ID=%%A"
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command ^
  "(Get-Content '%OAUTH_JSON%' -Raw | ConvertFrom-Json).client_secret"`) do set "GOOGLE_DEFAULT_CLIENT_SECRET=%%A"
start "" "%CHROME_EXE%" --user-data-dir="%PROFILE_DIR%" ^
  --disable-features=DiceHeaderVersion2 --no-first-run --no-default-browser-check
endlocal
