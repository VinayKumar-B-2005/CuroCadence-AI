.PHONY: install playground run test

AGENT_DIR = app

install:
	uv sync

playground:
	uv run adk web $(AGENT_DIR) --host 127.0.0.1 --port 18081

run:
	uv run uvicorn $(AGENT_DIR).fast_api_app:app --host 127.0.0.1 --port 18080 --reload

test:
	uv run pytest tests/ -v
