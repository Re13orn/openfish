.PHONY: ci-local test lint-consistency

ci-local:
	@bash scripts/ci_local.sh

test:
	@python -m pytest -q

lint-consistency:
	@python scripts/check_event_consistency.py
