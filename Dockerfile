FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# private/ (the sweep xprv) should be a mounted volume, not baked into the image.
VOLUME ["/app/private", "/app/data"]
ENV OPG_DB_PATH=/app/data/optimus_gateway.db
ENV OPG_SWEEP_KEY_PATH=/app/private/gateway_sweep/account.xprv

EXPOSE 8000
CMD ["python", "run.py", "serve"]
