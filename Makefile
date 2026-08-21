.PHONY: installer simulateur serveur test verifier

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

verifier:
	.venv/bin/python -m compileall -q agent tests && echo "Code valide"
