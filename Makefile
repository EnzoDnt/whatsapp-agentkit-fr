.PHONY: installer simulateur serveur test test-pg postgres verifier

# Python 3.10 est le plancher réel — FastAPI 0.141 l'exige — et 3.12 est ce
# que tourne l'image Docker. `uv` choisit seul la bonne version quand il est
# installé. Sans lui on retombe sur le python3 du système, qui vaut 3.9 sur
# macOS : l'environnement se créait quand même, et l'installation mourait
# beaucoup plus loin sur « No matching distribution found for fastapi », un
# message qui ne dit ni quelle version manque, ni où la trouver.
PYTHON ?= python3

installer:
	@if command -v uv >/dev/null 2>&1; then \
	  uv venv --python 3.12 .venv; \
	else \
	  $(PYTHON) -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || { \
	    echo "Python 3.10 minimum (3.12 recommandé) — trouvé : $$($(PYTHON) -V 2>&1)"; \
	    echo "Avec une autre version : make installer PYTHON=python3.12"; \
	    exit 1; \
	  }; \
	  $(PYTHON) -m venv .venv; \
	fi
	@.venv/bin/python -m pip install -q --upgrade pip 2>/dev/null || true
	@if command -v uv >/dev/null 2>&1; then \
	  uv pip install --python .venv/bin/python -r requirements-dev.txt; \
	else \
	  .venv/bin/python -m pip install -q -r requirements-dev.txt; \
	fi

simulateur:
	.venv/bin/uvicorn agent.main:app --port $${PORT:-8000} --reload

serveur:
	.venv/bin/uvicorn agent.main:app --host 0.0.0.0 --port $${PORT:-8000}

test:
	.venv/bin/python -m pytest tests/ -q

# PostgreSQL de test. Sans lui, tests/test_postgres.py est simplement ignoré :
# le kit doit rester installable sans Docker.
postgres:
	docker rm -f agentkit-pg-test 2>/dev/null || true
	docker run -d --name agentkit-pg-test \
	  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=agentkit \
	  -p 55432:5432 postgres:17-alpine
	@until docker exec agentkit-pg-test pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL prêt sur 127.0.0.1:55432"

test-pg: postgres
	.venv/bin/python -m pytest tests/test_postgres.py -q

verifier:
	.venv/bin/python -m compileall -q agent tests && echo "Code valide"
