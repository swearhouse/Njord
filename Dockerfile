FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
COPY static static
ENV NJORD_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
# Single worker: JSON file access is guarded by an in-process lock
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8000", "app:app"]
