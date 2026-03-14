PYTHON ?= python3
PIP = $(PYTHON) -m pip

all: test

install:
	$(PIP) install -e .

dev-install:
	$(PIP) install -e ".[dev]"

test:
	$(PYTHON) -m pytest

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist .pytest_cache *.egg-info

.PHONY: all install dev-install test build clean