FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# Local cursor-sdk agents restricted to MCP tools (agents/cursor_runtime.py); cloud stays opt-in.
ENV OU_CURSOR_RUNTIME=local
ENV OU_SCOUT_MAX_MARKETS=5
# ADR-0002 24 h fallback: matching agents attest, then permissionless resolveFallback.
ENV OU_FALLBACK_POLICY=attest

CMD ["python", "-m", "job"]
