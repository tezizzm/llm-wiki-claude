PYTHON=python3

venv:
	$(PYTHON) -m venv .venv

install:
	. .venv/bin/activate && pip install -r requirements.txt

install-editable:
	. .venv/bin/activate && pip install --no-build-isolation -e '.[dev]'

config:
	. .venv/bin/activate && python scripts/show_config.py

sync:
	. .venv/bin/activate && python scripts/sync.py

sync-dry-run:
	. .venv/bin/activate && python scripts/sync.py --dry-run

sync-prune:
	. .venv/bin/activate && python scripts/sync.py --prune

sync-prune-dry-run:
	. .venv/bin/activate && python scripts/sync.py --dry-run --prune

refresh:
	. .venv/bin/activate && python scripts/sync.py --prune && python scripts/ingest.py

refresh-fast:
	. .venv/bin/activate && python scripts/sync.py && python scripts/ingest.py

test:
	. .venv/bin/activate && pytest

release-check:
	. .venv/bin/activate && PYTHONPYCACHEPREFIX=/tmp/llm-wiki-pycache python3 -m py_compile scripts/*.py && pytest && llm-wiki --version

doctor:
	. .venv/bin/activate && llm-wiki doctor

cli-help:
	. .venv/bin/activate && llm-wiki

ingest:
	. .venv/bin/activate && python scripts/ingest.py

query:
	. .venv/bin/activate && python scripts/query.py

lint:
	. .venv/bin/activate && python scripts/lint.py
