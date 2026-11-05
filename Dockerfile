FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Amsterdam \
    HOST=0.0.0.0 \
    PORT=5000 \
    CARPOOL_DB=/data/carpool.sqlite3 \
    SECRET_KEY_FILE=/data/secret_key.txt \
    VAPID_KEY_FILE=/data/vapid_private.pem

# tzdata: zodat 'vandaag' in Nederlandse tijd wisselt, niet in UTC.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py push.py start.py importeer_agenda.py reset_wachtwoord.py ./
COPY templates templates
COPY static static

RUN useradd --system --uid 1000 carpool && mkdir /data && chown carpool /data
USER carpool
VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/login', timeout=4)" || exit 1

CMD ["python", "start.py"]
