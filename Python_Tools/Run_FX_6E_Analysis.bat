@echo off
setlocal
cd /d "%~dp0"
set "RESULTS_DIR=Z:\04_Code\Amibroker\Reports\Daily_OptResults"
python run_fx_6e_analysis.py "%RESULTS_DIR%"
echo.
pause
