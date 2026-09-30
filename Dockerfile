FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERMES_HOME=/run/gigachat

WORKDIR /proxy
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gigachat_proxy.py .
RUN useradd --create-home --uid 10002 proxyuser
USER proxyuser
EXPOSE 8766
HEALTHCHECK --interval=20s --timeout=10s --retries=5 CMD python -c "import urllib.request,sys; data=urllib.request.urlopen('http://127.0.0.1:8766/health').read(); sys.exit(0 if b'\\\"ok\\\":true' in data else 1)"
CMD ["uvicorn", "gigachat_proxy:app", "--host", "0.0.0.0", "--port", "8766"]
