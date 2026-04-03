FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INVOICE_COLLECT_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/uploads

EXPOSE 8088

ENV INVOICE_COLLECT_HOST=0.0.0.0 \
    INVOICE_COLLECT_PORT=8088

CMD ["sh", "-c", "mkdir -p \"${INVOICE_COLLECT_DATA_DIR}/uploads\" && exec uvicorn main:app --host \"${INVOICE_COLLECT_HOST}\" --port \"${INVOICE_COLLECT_PORT}\""]
