PYTHON:=python
VENV:=venv

.PHONY: test venv

venv:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install -e .

test:
	# Ensure tests can import package; editable install recommended
	export PYTHONPATH=$(shell pwd)
	pytest -q tests/test_wfa_cpcv.py tests/test_wfa_cpcv_integration.py tests/test_calibrate.py
