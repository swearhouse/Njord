FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NJORD_DATA_DIR=/var/lib/njord

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir . \
    && useradd --system --no-create-home --shell /usr/sbin/nologin njord \
    && mkdir -p /var/lib/njord \
    && chown njord:njord /var/lib/njord

USER njord

EXPOSE 8000

# Single process: JSON file access is guarded by an in-process lock
CMD ["python", "-m", "njord"]
