#!/bin/bash
# Entrypoint for remarkable-gtd Docker container
set -e

# ── SSH key (for private git repos via SSH URL) ──────────────────────────────
if [ -f "/run/secrets/ssh_private_key" ]; then
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    cp /run/secrets/ssh_private_key ~/.ssh/id_ed25519
    chmod 600 ~/.ssh/id_ed25519
    ssh-keyscan -H gitlab.com github.com >> ~/.ssh/known_hosts 2>/dev/null || true
fi

# ── Git identity (needed to commit vault changes back) ───────────────────────
[ -n "${GIT_USER_NAME:-}"  ] && git config --global user.name  "$GIT_USER_NAME"
[ -n "${GIT_USER_EMAIL:-}" ] && git config --global user.email "$GIT_USER_EMAIL"

# ── HTTPS credentials (for private repos via token) ──────────────────────────
# Set GIT_TOKEN=<personal-access-token> in .env for private HTTPS repos.
if [ -n "${GIT_TOKEN:-}" ]; then
    git config --global credential.helper store
    _host=$(echo "${GTD_URL:-}" | sed 's|https://||;s|/.*||')
    echo "https://oauth2:${GIT_TOKEN}@${_host}" >> ~/.git-credentials
fi

# ── reMarkable credentials ───────────────────────────────────────────────────
if [ -n "${RMAPI_DEVICE_TOKEN:-}" ]; then
    mkdir -p ~/.config/rmapi
    printf '{"devicetoken":"%s","usertoken":"%s"}\n' \
        "$RMAPI_DEVICE_TOKEN" "${RMAPI_USER_TOKEN:-}" \
        > ~/.config/rmapi/rmapi.conf
    chmod 600 ~/.config/rmapi/rmapi.conf
fi

exec uv --directory /app run "$@"
