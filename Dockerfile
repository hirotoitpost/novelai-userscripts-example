# アプリ本体(バックエンド + ビルドした画面)のイメージ。使い方は docs/docker.md。
# 開発はこれまでどおり scripts/dev-ctl.ps1(Vite :5173 + バックエンド :8000)。このイメージは「配る・動かす」用。

# ---- 画面をビルドする ----
FROM node:22.11.0-slim AS web
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY public ./public
COPY src/javascript ./src/javascript
RUN npm run build

# ---- バックエンド ----
FROM python:3.13.1-slim-bookworm
# fonts-noto-cjk: 吹き出し・描き文字の日本語フォント(Windows のフォントが無いとき)
# libgl1・libglib2.0-0: OCR(rapidocr が使う OpenCV)に要る
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    APP_IN_CONTAINER=1 \
    SERVE_FRONTEND=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/python ./src/python
COPY scripts ./scripts
COPY README.md ./
COPY --from=web /app/dist ./dist
# 管理ページに出す、イメージを作った日時
RUN date "+%Y-%m-%d %H:%M:%S %Z" > /app/BUILD_INFO

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=5)"
# ログを docker compose logs と data/run/container/backend.err.log(管理ページのログの欄)の両方に出す
CMD ["python", "scripts/container_start.py"]
