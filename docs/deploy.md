# Deploying OzWatch

> **Using Google Cloud's free VM?** Follow the click-by-click guide in
> [deploy-gcp.md](deploy-gcp.md) instead.

OzWatch needs **one always-on Linux machine**: the pollers and the AIS
stream run continuously inside the API process. Free hosting tiers that put
apps to sleep when idle won't work. Visitor numbers don't affect upstream API
usage, because the server fetches each source once and shares it with every
browser.

**Sizing:** 1 vCPU and 1 GB RAM is enough (API + PostGIS + Caddy), with
~5 GB of disk. The 24 h history keeps the database small.

Options that fit:

| Option | Cost | Notes |
|---|---|---|
| Oracle Cloud "Always Free" (Ampere A1 VM) | Free | Generous, but sign-up needs card verification and capacity can be scarce. Also open ports 80/443 in the VCN security list, not just the VM firewall. |
| Small VPS (Hetzner, DigitalOcean, Vultr, Linode…) | ~A$6–10/month | Simplest. Pick a Sydney or Singapore region for low latency to the Australian data sources. |

**CPU architecture:** the app and Caddy images support both x86 (amd64) and
ARM (arm64). The `postgis/postgis` image has historically been published for
amd64 only, so on an ARM VM (e.g. Oracle's Ampere A1) check
`docker manifest inspect postgis/postgis:16-3.4` first. If arm64 isn't
listed, either pick an amd64 VM (Oracle's free AMD micro instance, 1 GB RAM,
is enough) or switch the `db` image to a multi-arch PostGIS build.

## 1. Server

Ubuntu 24.04, then:

```bash
# Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# Firewall: SSH + web only
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

## 2. Domain

Create a DNS **A record** (e.g. `ozwatch.yourdomain.com`) pointing at the
server's public IP. Caddy needs this before it can get a certificate. No
domain? A free subdomain from DuckDNS works.

## 3. App

```bash
git clone https://github.com/pupshii/OzWatch.git && cd OzWatch
cp .env.example .env
nano .env   # API keys, DOMAIN=ozwatch.yourdomain.com, a strong POSTGRES_PASSWORD, and
            # COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml (so plain `docker compose` includes Caddy)
docker compose up -d --build
```

Open `https://ozwatch.yourdomain.com`. The first certificate takes a few
seconds. Check `https://ozwatch.yourdomain.com/health`.

> Set `POSTGRES_PASSWORD` **before** the first start. Postgres only reads it
> when the data volume is first created.

## 4. Updating

```bash
git pull
docker compose up -d --build
```

Browsers reconnect by themselves after the restart (the live stream retries
every 5 s).

## 5. Operations

- **Logs:** `docker compose logs -f api`
- **Health:** `/health` returns per-source status and is suitable for a free
  uptime monitor (e.g. UptimeRobot) to alert you if the site goes down.
- **Backups:** the data is a 24 h rolling window, so backups are optional.
  If wanted: `docker compose exec db pg_dump -U ozwatch ozwatch | gzip > backup.sql.gz`
- **Disk:** `docker system prune` occasionally removes old images after updates.

## Licence reminders for a public site

- **OpenSky:** non-commercial use only, which a portfolio site satisfies.
  Don't add ads or paid features.
- **BoM** data is deliberately not used (see `docs/data-sources.md`).
- Keep the in-app attribution panel visible. CC BY and the basemap
  (OpenStreetMap/OpenMapTiles) require it.
