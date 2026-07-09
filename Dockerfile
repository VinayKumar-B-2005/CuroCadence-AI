FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir uv
RUN uv sync --frozen || uv sync

EXPOSE 10000

CMD uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port $PORT
