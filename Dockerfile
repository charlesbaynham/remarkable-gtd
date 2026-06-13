# remarkable-gtd — multi-stage build
# Stage 1: build rmapi from source
FROM golang:1.23-bookworm AS rmapi-builder
RUN git clone --depth 1 https://github.com/ddvk/rmapi.git /src/rmapi
WORKDIR /src/rmapi
RUN go build -o /usr/local/bin/rmapi .

# Stage 2: runtime
FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ssh-client ca-certificates \
    libzbar0 tesseract-ocr poppler-utils \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rmapi-builder /usr/local/bin/rmapi /usr/local/bin/rmapi

# Install uv and Python package
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml .
COPY src/ ./src/
RUN uv sync

# Install Playwright Chromium into a fixed system path accessible to all users
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN uv run playwright install --with-deps chromium

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gtd-daily"]
