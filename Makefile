# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

# Development tasks for the Energie project (GNU make).
#
# Works on Windows (recipes run in cmd.exe, also when make is started from
# PowerShell or Git Bash) and on Linux/macOS.
#
#   make venv      create .venv and install the project with all extras
#   make test      run the tests
#   make run       start the web app at http://localhost:8510 (override with ARGS="...")
#   make demo      try the app on invented data at http://localhost:8511
#   make screenshots  retake the README screenshots from the demo (Edge or Chrome)
#   make deploy    set up the collector on the Raspberry Pi with Ansible (WSL on Windows)
#   make diagrams  export docs/diagrams/*.drawio to SVG with the draw.io desktop app
#
# The project uses Python 3.12 (PYTHON_VERSION). The base interpreter is found
# with the py launcher on Windows and as python3.12 elsewhere, independently of
# which python comes first on the PATH (Miniconda included).

PYTHON_VERSION := 3.12

ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    .SHELLFLAGS := /c
    PYTHON := py -$(PYTHON_VERSION)
    VENV_PY := .venv\Scripts\python.exe
    # Ansible does not run on Windows: the playbooks run in WSL (bash -l for ~/.local/bin)
    IN_DEPLOY := wsl --cd "$(CURDIR)/deploy" bash -lc
else
    PYTHON := python$(PYTHON_VERSION)
    VENV_PY := .venv/bin/python
    IN_DEPLOY := cd deploy && bash -lc
endif

# draw.io desktop app for make diagrams; override with DRAWIO="path/to/draw.io".
ifeq ($(OS),Windows_NT)
    DRAWIO_USER := $(subst \,/,$(LOCALAPPDATA))/Programs/draw.io/draw.io.exe
    ifneq ($(wildcard $(DRAWIO_USER)),)
        DRAWIO ?= $(DRAWIO_USER)
    else
        DRAWIO ?= C:/Program Files/draw.io/draw.io.exe
    endif
else
    DRAWIO ?= drawio
endif
DIAGRAMS := $(wildcard docs/diagrams/*.drawio)

VENV_CFG := .venv/pyvenv.cfg
STAMP := .venv/.installed
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help venv test run demo screenshots deploy discover diagrams clean distclean recreate

help:
	@echo Targets:
	@echo   make venv       create .venv and install the project with all extras
	@echo   make test       run the tests with pytest
	@echo   make run        start the web app at http://localhost:8510; options with ARGS="--port ..."
	@echo   make demo       try the app on invented data at http://localhost:8511 (your database is not used)
	@echo   make screenshots  retake the README screenshots in docs/images from the demo
	@echo   make deploy     set up the collector on the Raspberry Pi (Ansible; deploy/README.md)
	@echo   make discover NET=192.168.178.0/24  list the Shelly devices for deploy/inventory.yml
	@echo   make diagrams   export docs/diagrams/*.drawio to SVG (draw.io desktop, DRAWIO=...)
	@echo   make clean      remove caches and build metadata
	@echo   make distclean  clean and remove .venv
	@echo   make recreate   distclean, then venv
	@echo Python $(PYTHON_VERSION), base interpreter: $(PYTHON)

# .venv is created only once. Re-creating an existing one would overwrite its
# python.exe, which fails while an IDE or the running app is using it.
$(VENV_CFG):
	$(PYTHON) scripts/check_venv.py $(PYTHON_VERSION)
	$(PYTHON) -m venv .venv

# The stamp file is rewritten after every install, so the packages are
# refreshed whenever pyproject.toml or this Makefile changes. An existing
# .venv of another Python version is never upgraded in place: check_venv.py
# stops with a hint to run make recreate.
$(STAMP): pyproject.toml Makefile $(VENV_CFG)
	$(PYTHON) scripts/check_venv.py $(PYTHON_VERSION)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e ".[dev]"
	$(VENV_PY) -c "open('$(STAMP)', 'w').close()"

venv: $(STAMP)

test: $(STAMP)
	$(VENV_PY) -m pytest

run: $(STAMP)
	$(VENV_PY) -m energie $(ARGS)

demo: $(STAMP)
	$(VENV_PY) -m energie demo $(ARGS)

screenshots: $(STAMP)
	$(VENV_PY) scripts/readme_screenshots.py $(ARGS)

INVENTORY_CHECK := $(PYTHON) -c "import os, sys; os.path.exists('deploy/inventory.yml') or sys.exit('Copy deploy/inventory.example.yml to deploy/inventory.yml and fill it in first')"

deploy:
	$(INVENTORY_CHECK)
	$(IN_DEPLOY) "ansible-playbook -i inventory.yml site.yml --ask-become-pass $(ARGS)"

discover:
	$(INVENTORY_CHECK)
	$(IN_DEPLOY) "ansible-playbook -i inventory.yml discover.yml -e net=$(NET)"

# One SVG per diagram, rebuilt when its .drawio changes. Fonts are not embedded
# (small files); the white page background comes from the diagram, so the
# images read well in light and dark mode.
diagrams: $(DIAGRAMS:.drawio=.svg)

docs/diagrams/%.svg: docs/diagrams/%.drawio
	"$(DRAWIO)" --export --format svg --embed-svg-fonts false --theme light --border 10 --output $@ $<

clean:
	$(PYTHON) -c "import glob, shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', 'build', *glob.glob('src/*.egg-info'), *glob.glob('*/**/__pycache__', recursive=True)]]"

distclean: clean
	$(PYTHON) -c "import shutil; shutil.rmtree('.venv', ignore_errors=True)"

recreate: distclean
	$(MAKE) venv

