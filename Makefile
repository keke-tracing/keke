PYTHON?=python
SOURCES=keke setup.py

.PHONY: venv
venv:
	$(PYTHON) -m venv .venv
	source .venv/bin/activate && make setup
	@echo 'run `source .venv/bin/activate` to use virtualenv'

# The rest of these are intended to be run within the venv, where python points
# to whatever was used to set up the venv.

.PHONY: setup
setup:
	python -m pip install -Ue .[dev,test]

# Memory: Linux supports ulimit -v (virtual), macOS doesn't.
# Wall-clock: perl alarm(30) works on both platforms.
ifeq ($(shell uname),Linux)
    MEM_LIMIT := ulimit -v 2097152 &&
else
    MEM_LIMIT :=
endif
SAFETY := $(MEM_LIMIT) perl -e 'alarm(30); exec @ARGV or die' --

.PHONY: test
test:
	$(SAFETY) python -m coverage run -m keke.tests $(TESTOPTS)
	python -m coverage report

.PHONY: format
format:
	python -m ufmt format $(SOURCES)

.PHONY: lint
lint:
	python -m ufmt check $(SOURCES)
	python -m flake8 $(SOURCES)
	python -m checkdeps --allow-names keke,psutil keke
	mypy --strict --install-types --non-interactive keke

.PHONY: release
release:
	rm -rf dist
	python setup.py sdist bdist_wheel
	twine upload dist/*
