# Image de production de l'agent.
FROM python:3.12-slim

# PYTHONUNBUFFERED : sans lui, les journaux restent bloqués en mémoire tampon et
# n'apparaissent dans la console de l'hébergeur qu'avec plusieurs minutes de
# retard — exactement quand on en a besoin, c'est-à-dire quand ça va mal.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg : convertit les notes vocales WhatsApp (Ogg/Opus) en WAV 16 kHz mono
# avant transcription. Les fournisseurs listent « audio/ogg » sans jamais
# confirmer Opus — transcoder supprime toute une classe de pannes silencieuses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Les dépendances avant le code : tant que requirements.txt ne bouge pas, cette
# couche est réutilisée et les redéploiements prennent quelques secondes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# L'agent tourne sans privilèges : si quelqu'un parvient à exécuter du code dans
# le conteneur, il n'est pas root pour autant.
RUN useradd --create-home --shell /usr/sbin/nologin agentkit \
    && chown -R agentkit:agentkit /app
USER agentkit

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('PORT','8000')}/\", timeout=4).status==200 else 1)"

# Forme shell À DESSEIN : ${PORT} doit être remplacé au démarrage. Railway,
# Coolify et la plupart des hébergeurs imposent le port par variable. Écrit en
# dur, le conteneur démarre normalement mais ne reçoit jamais aucun trafic, et
# rien dans les journaux ne l'explique.
CMD uvicorn agent.main:app --host 0.0.0.0 --port ${PORT:-8000}
