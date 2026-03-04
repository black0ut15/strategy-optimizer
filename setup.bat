@echo off
echo ==========================================
echo   Strategy Optimizer - Setup
echo ==========================================
echo.

:: Check for Rust
where cargo >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Rust not found. Installing via rustup...
    echo     Download from https://rustup.rs and run the installer.
    echo     Then restart this script.
    pause
    exit /b 1
) else (
    echo [OK] Rust found: 
    cargo --version
)

:: Check for Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Python not found. Install from https://python.org
    pause
    exit /b 1
) else (
    echo [OK] Python found:
    python --version
)

:: Install Python dependencies for data fetchers
echo.
echo Installing Python dependencies for data fetchers...
pip install requests pandas pyarrow --quiet

:: Check for Node
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] Node.js not found. Install from https://nodejs.org
    pause
    exit /b 1
) else (
    echo [OK] Node.js found:
    node --version
)

echo.
echo Installing frontend dependencies...
call npm install

echo.
echo ==========================================
echo   Ready! Choose an option:
echo ==========================================
echo   1. npm run tauri dev    (development mode with hot reload)
echo   2. npm run tauri build  (create distributable installer)
echo ==========================================
echo.

set /p choice="Enter 1 or 2: "
if "%choice%"=="1" (
    npm run tauri dev
) else if "%choice%"=="2" (
    npm run tauri build
    echo.
    echo Build complete! Check src-tauri\target\release\bundle\ for installers.
) else (
    echo Invalid choice. Run manually:
    echo   npm run tauri dev
    echo   npm run tauri build
)

pause
