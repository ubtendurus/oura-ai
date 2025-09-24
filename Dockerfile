FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PIP_NO_CACHE_DIR=1

ARG APP_USER=appuser

WORKDIR /app

COPY requirements.txt ./

RUN set -eux;     apt-get update;     apt-get install -y --no-install-recommends tzdata;     pip install --no-cache-dir -r requirements.txt;     apt-get clean;     rm -rf /var/lib/apt/lists/*;     useradd --create-home --shell /bin/bash ${APP_USER}

COPY . .

RUN mkdir -p var && chown -R ${APP_USER}:${APP_USER} /app

USER ${APP_USER}

EXPOSE 8000

CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
