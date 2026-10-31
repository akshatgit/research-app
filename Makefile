PY := .venv/bin/python

.PHONY: setup test test-py test-js test-live run

setup:
	python3 -m venv .venv && .venv/bin/pip install -q 'pytest>=8'

test: test-py test-js

test-py:
	$(PY) -m pytest -q

test-js:
	node --test tests/js/

test-live:
	RESEARCH_APP_LIVE=1 $(PY) -m pytest tests/test_live.py -v

run:
	$(PY) -m research_app
