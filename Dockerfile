FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY *.py stations.json ./
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "900", "cloud_service:app"]
