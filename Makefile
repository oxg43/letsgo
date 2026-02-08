.PHONY: test lint report checks tune

test:
	python -m pytest tests/ -v --tb=short

lint:
	python -m py_compile paper_trade_alerts.py
	python -m py_compile scripts/weekly_report.py
	python -m py_compile scripts/auto_tune_and_suggest.py
	@echo "All files compile OK"

report:
	python scripts/weekly_report.py

report-dry:
	python scripts/weekly_report.py --dry-run

tune:
	python scripts/auto_tune_and_suggest.py

checks: test lint report-dry
	@echo "All checks passed"

once:
	python paper_trade_alerts.py --once

run:
	python paper_trade_alerts.py
