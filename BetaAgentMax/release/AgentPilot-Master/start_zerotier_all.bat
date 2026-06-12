@echo off
setlocal EnableDelayedExpansion
title NixControl - ZeroTier + Coordinator All-in-One
cd /d "%~dp0"

REM ============================================================
REM  Script todo-en-uno:
REM   1. Arranca el servicio ZeroTier (admin)
REM   2. Te une a la red ZeroTier (primera vez)
REM   3. Detecta tu IP virtual ZeroTier
REM   4. Arranca el coordinador con esa IP
REM ============================================================

REM ─── Pedir admin ─────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando admin (necesario para ZeroTier)...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

color 0B
echo.
echo  ============================================================
echo    NixControl - ZeroTier + Coordinator (todo en uno)
echo  ============================================================
echo.

REM ─── Encontrar zerotier-cli ──────────────────────────────────
echo  [1/5] Buscando ZeroTier...
set ZT_CLI=
if exist "C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat" set ZT_CLI="C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat"
if exist "C:\ProgramData\ZeroTier\One\zerotier-cli.bat"          set ZT_CLI="C:\ProgramData\ZeroTier\One\zerotier-cli.bat"
if exist "C:\Program Files\ZeroTier\One\zerotier-cli.bat"        set ZT_CLI="C:\Program Files\ZeroTier\One\zerotier-cli.bat"
if exist "C:\Program Files (x86)\ZeroTier\One\zerotier-one_x64.exe" set ZT_CLI="C:\Program Files (x86)\ZeroTier\One\zerotier-one_x64.exe" -q
if exist "C:\ProgramData\ZeroTier\One\zerotier-one_x64.exe"      set ZT_CLI="C:\ProgramData\ZeroTier\One\zerotier-one_x64.exe" -q
if exist "C:\Program Files\ZeroTier\One\zerotier-one_x64.exe"    set ZT_CLI="C:\Program Files\ZeroTier\One\zerotier-one_x64.exe" -q

if "!ZT_CLI!"=="" (
    color 0C
    echo  [ERROR] ZeroTier no encontrado.
    echo  Descargalo desde: https://www.zerotier.com/download/
    start "" "https://www.zerotier.com/download/"
    pause
    exit /b 1
)
echo  [OK] %ZT_CLI%
echo.

REM ─── Arrancar servicio ZeroTier ──────────────────────────────
echo  [2/5] Iniciando servicio ZeroTier...
sc query ZeroTierOneService | findstr /C:"RUNNING" >nul
if errorlevel 1 (
    echo  Arrancando servicio...
    net start ZeroTierOneService >nul 2>&1
    timeout /t 3 >nul
)
echo  [OK] Servicio activo.
echo.

REM ─── Listar redes actuales ───────────────────────────────────
echo  [3/5] Redes ZeroTier conectadas:
echo.
%ZT_CLI% listnetworks
echo.

REM ─── Verificar si ya esta unido a alguna ─────────────────────
%ZT_CLI% listnetworks | findstr /R "[0-9a-f]\{16\}" >nul
if errorlevel 1 (
    echo  [!] No estas unido a ninguna red ZeroTier todavia.
    echo.
    echo  Necesitas:
    echo    1. Una cuenta gratis en https://my.zerotier.com
    echo    2. Crear una red (boton "Create A Network")
    echo    3. Copiar el Network ID (16 caracteres, tipo abc123def456789a)
    echo    4. Pegarlo aqui abajo
    echo.
    set /p ZT_NET_ID="  Network ID: "
    if "!ZT_NET_ID!"=="" (
        echo  [ERROR] Network ID requerido.
        pause
        exit /b 1
    )
    echo.
    echo  Uniendote a la red !ZT_NET_ID!...
    %ZT_CLI% join !ZT_NET_ID!
    echo.
    echo  [IMPORTANTE] Ahora ve a https://my.zerotier.com/network/!ZT_NET_ID!
    echo               y marca el checkbox "Auth" de tu dispositivo.
    echo               (Tu Node ID aparece arriba)
    echo.
    pause
    timeout /t 3 >nul
)

REM ─── Obtener IP virtual de ZeroTier ──────────────────────────
echo  [4/5] Obteniendo tu IP virtual de ZeroTier...
echo.
set ZT_IP=
for /f "tokens=*" %%i in ('%ZT_CLI% listnetworks ^| findstr /R "[0-9a-f]\{16\}"') do (
    for %%j in (%%i) do (
        echo %%j | findstr /R "^[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/" >nul
        if not errorlevel 1 (
            for /f "tokens=1 delims=/" %%k in ("%%j") do set ZT_IP=%%k
        )
    )
)

if "!ZT_IP!"=="" (
    color 0E
    echo  [WARN] No se pudo detectar tu IP virtual ZeroTier automaticamente.
    echo.
    echo  Mira arriba en "listnetworks" - tu IP aparece despues del Network ID.
    echo  Formato tipico: 10.144.x.x o 192.168.196.x
    echo.
    set /p ZT_IP="  Pega tu IP virtual ZeroTier aqui: "
    if "!ZT_IP!"=="" (
        echo  [ERROR] IP requerida.
        pause
        exit /b 1
    )
)
echo  [OK] Tu IP ZeroTier: !ZT_IP!
echo.

REM ─── Localizar venv ──────────────────────────────────────────
echo  [5/5] Arrancando coordinator federado...
set VENV=..\.venv
if not exist "%VENV%\Scripts\python.exe" (
    if exist "C:\Users\agust\Desktop\Pruebas1\NixControl\.venv\Scripts\python.exe" (
        set VENV=C:\Users\agust\Desktop\Pruebas1\NixControl\.venv
    ) else (
        echo  [ERROR] Venv no encontrado.
        pause
        exit /b 1
    )
)

echo.
echo  ============================================================
echo    COMPARTI ESTO CON TU AMIGO:
echo  ============================================================
echo.
echo    IP del coordinador:  !ZT_IP!
echo    Puerto:              12356
echo.
echo    Tu amigo necesita:
echo      1. ZeroTier instalado + unido a la misma red
echo      2. Network ID arriba (decirselo a tu amigo tambien)
echo      3. Ejecutar start_contributor_smart.bat con esa IP
echo  ============================================================
echo.

REM ─── Lanzar coordinator ──────────────────────────────────────
"%VENV%\Scripts\python.exe" coordinator.py ^
    --port 12356 ^
    --min_contributors 1 ^
    --max_contributors 100 ^
    --rounds 3 ^
    --epochs_per_round 1 ^
    --train_dir "%CD%"

echo.
echo  Coordinador finalizado.
pause
endlocal
