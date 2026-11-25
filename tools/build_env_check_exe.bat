@echo off
rem Rebuild the environment checker bundled with the Python package.
setlocal
cd /d "%~dp0.."
set "ASSET_DIR=src\ckks_lite\_bin"
if not exist "%ASSET_DIR%" mkdir "%ASSET_DIR%"

python -m PyInstaller --noconfirm --onefile --console --clean ^
  --name ckks_env_check ^
  --distpath "%ASSET_DIR%" --workpath build\env_check --specpath build\env_check ^
  tools\env_check.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)
if not exist "%ASSET_DIR%\ckks_env_check.exe" (
  echo.
  echo Build finished but the executable was not found.
  exit /b 1
)

echo.
echo Done: %CD%\%ASSET_DIR%\ckks_env_check.exe
