PYTHON ?= python3
SHELL := /bin/bash
SHELL_FILES := linux/ced linux/certinfo linux/lsswap linux/pls openshift/kdf openshift/ocprems
PYTHON_FILES := openshift/ocmt linux/gencl

.PHONY: check syntax lint test
check: syntax lint test

syntax:
	@for file in $(SHELL_FILES); do bash -n "$$file" || exit; done
	$(PYTHON) -c 'import ast, sys; from pathlib import Path; [ast.parse(Path(p).read_text(), filename=p) for p in sys.argv[1:]]' $(PYTHON_FILES)

lint:
	shellcheck $(SHELL_FILES)
	ruff check tests $(PYTHON_FILES)

# Network access is limited to a loopback TLS fixture; no real cluster is contacted.
test:
	$(PYTHON) -m unittest discover -s tests -v
