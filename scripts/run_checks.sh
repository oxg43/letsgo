#!/usr/bin/env bash
# run_checks.sh — CI runner: tests, lint, weekly report dry-run
set -e
cd "$(dirname "$0")/.."

echo "=== 1. Running pytest ==="
python -m pytest tests/ -v --tb=short

echo ""
echo "=== 2. Syntax check (py_compile) ==="
python -m py_compile paper_trade_alerts.py
python -m py_compile scripts/weekly_report.py
python -m py_compile scripts/auto_tune_and_suggest.py
echo "  All files compile OK"

echo ""
echo "=== 3. Weekly report dry-run ==="
python scripts/weekly_report.py --dry-run

echo ""
echo "=== ALL CHECKS PASSED ==="
