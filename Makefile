PYTHON:=python
VENV:=venv

.PHONY: test venv demo demo-ci

venv:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install -e .

test:
	# Ensure tests can import package; editable install recommended
	export PYTHONPATH=$(shell pwd)
	pytest -q tests/test_wfa_cpcv.py tests/test_wfa_cpcv_integration.py tests/test_calibrate.py

demo:
	# Run the calibrator demo from the repository root; creates examples/calibrate_params_demo.json
	PYTHONPATH=$(shell pwd) $(PYTHON) examples/calibrate_demo.py

# CI-friendly demo: run without plotting (matplotlib optional) and exit non-zero on failures
# This target is intended for use in CI smoke checks.
demo-ci:
	PYTHONPATH=$(shell pwd) $(PYTHON) examples/calibrate_demo.py || (echo "Demo failed"; exit 1)
