PYTHON ?= python3
SHELL := /bin/bash
SHELL_FILES := linux/ced linux/certinfo linux/lsswap linux/pls openshift/kdf openshift/ocprems openshift/ogn

.PHONY: check syntax lint test
check: syntax lint test

syntax:
	@for file in $(SHELL_FILES); do bash -n "$$file" || exit; done
	$(PYTHON) -c 'import ast; from pathlib import Path; ast.parse(Path("openshift/ocmt").read_text(), filename="openshift/ocmt")'

lint:
	shellcheck $(SHELL_FILES)
	ruff check tests openshift/ocmt

# Network access is limited to a loopback TLS fixture; no real cluster is contacted.
test:
	$(PYTHON) -m unittest discover -s tests -v
