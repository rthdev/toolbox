PYTHON ?= python3
SHELL := /bin/bash
SHELL_FILES := linux/ced linux/certinfo linux/gkc linux/lsswap linux/pls openshift/kdf openshift/ocprems $(wildcard lib/*.sh)

.PHONY: check syntax lint test
check: syntax lint test

syntax:
	@for file in $(SHELL_FILES); do bash -n "$$file" || exit; done
	$(PYTHON) -c 'import ast, pathlib; ast.parse(pathlib.Path("openshift/ocptool").read_text())'

lint:
	shellcheck -x $(SHELL_FILES)
	ruff check tests

# Network access is limited to a loopback TLS fixture; no real cluster is contacted.
test:
	$(PYTHON) -m unittest discover -s tests -v
