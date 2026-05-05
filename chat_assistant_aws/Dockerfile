FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf upgrade -y && dnf install -y python3.12 python3.12-pip && dnf clean all && \
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

WORKDIR /app

COPY requirements.txt .
RUN pip3.12 install --no-cache-dir -r requirements.txt

COPY main.py config.py session.py prompts.py rate_limit.py logging_service.py log_shipper.py ssm_config.py ./
COPY static/ ./static/

ENV DEPLOY_ENV=local
ENV LOCAL_LOG_DIR=/app/logs
ENV BEDROCK_REGION=us-east-1

RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
