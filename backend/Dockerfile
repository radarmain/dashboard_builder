# RDB API on Lambda via the AWS Lambda Web Adapter (runs uvicorn on :8000 inside the function).
# Build context = backend/. Target platform linux/arm64 (Graviton).
FROM public.ecr.aws/docker/library/python:3.12-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

WORKDIR /var/task
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY app ./app

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
