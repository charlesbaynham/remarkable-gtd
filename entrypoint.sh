#!/bin/bash
# Entrypoint for remarkable-gtd Docker container
# Sets up SSH, clones GTD repo if needed, then runs the requested command

set -e

# Setup SSH from mounted secret
SSH_KEY="${SSH_PRIVATE_KEY:-/run/secrets/ssh_private_key}"
if [ -f "$SSH_KEY" ]; then
    mkdir -p ~/.ssh
    cp "$SSH_KEY" ~/.ssh/id_ed25519
    chmod 600 ~/.ssh/id_ed25519
    # Add common Git hosts to known_hosts if not present
    ssh-keyscan -H gitlab.com >> ~/.ssh/known_hosts 2>/dev/null || true
    ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null || true
    echo "✓ SSH key configured"
fi

# Configure git identity (if env vars set)
if [ -n "$GIT_USER_NAME" ]; then
    git config --global user.name "$GIT_USER_NAME"
fi
if [ -n "$GIT_USER_EMAIL" ]; then
    git config --global user.email "$GIT_USER_EMAIL"
fi

# Clone GTD repo if not present
GTD_DIR="${GTD_DIR:-/data/gtd}"
if [ ! -d "$GTD_DIR/.git" ] && [ -n "$GTD_REPO_URL" ]; then
    echo "→ Cloning GTD repo..."
    git clone "$GTD_REPO_URL" "$GTD_DIR"
    echo "✓ Cloned to $GTD_DIR"
fi

# Run the requested command
exec "$@"
