@echo off
REM ============================================================================
REM   DAILY ONLY_HOME GENERATOR - Batch file za scheduled task
REM   Pokrece se svaki dan u 06:00 
REM ============================================================================

cd /d C:\Users\bibia\Desktop\DANAS

REM Prvo pokreni scraper da dohvati svjeze podatke
echo [%date% %time%] Starting scraper...
python -m odds_tracker.runner --once

REM Zatim generiraj ONLY_HOME signale
echo [%date% %time%] Generating ONLY_HOME signals...
python generate_daily_only_home.py

echo [%date% %time%] Done!
