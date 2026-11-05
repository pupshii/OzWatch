#!/usr/bin/env bash
# Keeps a DuckDNS name pointing at this server's current public IP.
# Reads DUCKDNS_SUBDOMAIN and DUCKDNS_TOKEN from the project's .env.
# Install as a cron job (every 5 minutes):
#   (crontab -l 2>/dev/null; echo "*/5 * * * * $HOME/OzWatch/deploy/duckdns-update.sh") | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${DUCKDNS_SUBDOMAIN:?set DUCKDNS_SUBDOMAIN in .env (just the name, e.g. ozwatch)}"
: "${DUCKDNS_TOKEN:?set DUCKDNS_TOKEN in .env (from duckdns.org)}"
# Empty ip= lets DuckDNS use the address the request comes from.
result=$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
echo "$(date -Is) ${result}" >> deploy/duckdns.log
tail -n 200 deploy/duckdns.log > deploy/duckdns.log.tmp && mv deploy/duckdns.log.tmp deploy/duckdns.log
[ "$result" = "OK" ]
