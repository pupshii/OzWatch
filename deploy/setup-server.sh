#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu/Debian VM (e.g. Google Cloud e2-micro).
# Safe to run more than once. Usage:  bash deploy/setup-server.sh
set -euo pipefail

# 1. Swap: the free e2-micro has only 1 GB RAM. 2 GB of swap stops Docker
#    builds and Postgres from being killed when memory runs short.
if ! sudo swapon --show | grep -q '/swapfile'; then
  echo "Creating 2 GB swap file..."
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 2. Docker Engine + Compose plugin (Docker's official install script).
if ! command -v docker >/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

# 3. Restart containers after a VM reboot (compose services use restart: unless-stopped).
sudo systemctl enable --now docker >/dev/null

echo
echo "Done. Log out and back in (or run 'newgrp docker') so 'docker' works without sudo."
free -h
