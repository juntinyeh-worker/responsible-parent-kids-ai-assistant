FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.py session.py api_secrets.py prompts.py rate_limit.py logging_service.py log_shipper.py auth.py ./
COPY static/ ./static/

# Default env vars (override via ECS task definition)
ENV DEPLOY_ENV=local
ENV LOCAL_LOG_DIR=/app/logs

RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
