FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV OU_CURSOR_RUNTIME=cloud
ENV OU_SCOUT_MAX_MARKETS=5

CMD ["python", "-m", "job"]
