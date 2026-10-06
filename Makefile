PYTHON ?= python3
SHELL := /bin/bash
SHELL_FILES := linux/ced linux/certinfo linux/gkc linux/lsswap linux/pls openshift/kdf openshift/ocprems

.PHONY: check syntax lint test
check: syntax lint test

syntax:
	@for file in $(SHELL_FILES); do bash -n "$$file" || exit; done

lint:
	shellcheck $(SHELL_FILES)
	ruff check tests

# Network access is limited to a loopback TLS fixture; no real cluster is contacted.
test:
	$(PYTHON) -m unittest discover -s tests -v
