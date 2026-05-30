# remarkable-gtd — GTD paper workflow for reMarkable 2
# Build: docker build -t remarkable-gtd .
# Run: docker compose run --rm gtd gtd-daily

FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ssh-client \
    libzbar0 \
    tesseract-ocr \
    poppler-utils \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install rmapi (Go binary)
RUN wget -qO- https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linux.zip \
    | funzip - > /usr/local/bin/rmapi \
    && chmod +x /usr/local/bin/rmapi

# Install Python package
WORKDIR /app
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[gen,scan,rm]"

# Install Playwright Chromium
RUN playwright install chromium

# Create non-root user
RUN useradd -m -s /bin/bash gtd

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER gtd
WORKDIR /home/gtd

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gtd-daily"]
