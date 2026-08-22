.PHONY: installer simulateur serveur test test-pg postgres verifier

installer:
	uv venv --python 3.12 .venv 2>/dev/null || python3 -m venv .venv
	.venv/bin/python -m pip install -q --upgrade pip 2>/dev/null || true
	uv pip install --python .venv/bin/python -r requirements-dev.txt

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
