# Deploy OzWatch on Google Cloud's free VM

Step-by-step, about 30 minutes. You end up with OzWatch live at
`https://<your-name>.duckdns.org`, with automatic HTTPS. For the general guide
(any VPS) see [deploy.md](deploy.md).

**What it costs:** Google's "Always Free" tier includes one `e2-micro` VM in
some US regions, a 30 GB standard disk and 1 GB/month of outbound traffic.
Two things may cost a little, because sources disagree and Google's terms
change:
- **The public IPv4 address:** Google's free-tier page says it's free, but
  some guides report about US$0.005/hour (~US$3.65/month).
- **Traffic to Australian visitors:** Google's free 1 GB of outbound traffic
  probably excludes Australia. OzWatch keeps this small: the browser loads
  map tiles and scripts from other services, and pauses the live stream in
  background tabs.

Step 1 sets a budget alert so any charge shows up in your inbox straight
away. Check the billing report after a couple of days (step 6).

---

## 1. Google Cloud account and a budget alert

1. Go to <https://console.cloud.google.com> and sign in with a Google account.
2. Accept the terms and **set up billing** (a card is required, even for
   free usage). New accounts get a 90-day US$300 trial credit on top of
   the Always Free tier.
3. Create a project: top bar → project picker → **New project** → name it
   `ozwatch` → **Create**, then select it.
4. **Budget alert:** ☰ menu → **Billing** → **Budgets & alerts** →
   **Create budget**. Scope it to the `ozwatch` project and set a target
   amount of **A$2** (or US$2). Keep the default alert thresholds (50/90/100%)
   and email alerts → **Finish**.

## 2. Create the VM

☰ menu → **Compute Engine** → **VM instances** (click **Enable** if asked for the
Compute Engine API; it takes a minute) → **Create instance**.

| Setting | Value | Why |
|---|---|---|
| Name | `ozwatch` | |
| Region | **us-west1 (Oregon)** | Free-tier region closest to Australia (~150 ms) |
| Zone | any (e.g. us-west1-b) | |
| Machine configuration | Series **E2** → machine type **e2-micro** | The free one. The cost estimate shown on the page doesn't subtract the free tier, so ignore it. |
| Boot disk → **Change** | OS **Ubuntu**, version **Ubuntu 24.04 LTS (x86/64)**, disk type **Standard persistent disk**, size **30 GB** | ⚠️ The default disk type ("Balanced") is **not** free; pick *Standard*. |
| Firewall | ✅ Allow HTTP traffic ✅ Allow HTTPS traffic | Caddy needs ports 80 and 443 |

Click **Create**. When it's running, copy its **External IP** from the list.

> If the free trial ends (90 days), Google asks you to "activate a full
> account" to keep resources running. Do it: the Always Free VM stays free.

## 3. Free web address (DuckDNS)

1. Go to <https://www.duckdns.org> and sign in with GitHub.
2. Type a subdomain (e.g. `ozwatch-yourname`) → **add domain**.
3. In **current ip**, paste the VM's external IP → **update ip**.
4. Copy your **token** from the top of the page; you'll need it in step 4.

Your site will be `https://ozwatch-yourname.duckdns.org`.

## 4. Install OzWatch on the VM

Back in **VM instances**, click **SSH** next to `ozwatch`. A terminal opens
in your browser. Google's Ubuntu images are "minimal" and don't include `git`,
so install the basics first, then run the rest:

```bash
sudo apt-get update && sudo apt-get install -y git nano cron curl ca-certificates
git clone https://github.com/pupshii/OzWatch.git
cd OzWatch
bash deploy/setup-server.sh      # adds swap memory + installs Docker (~2 min)
newgrp docker                    # lets you use docker without sudo in this session
cp .env.example .env
openssl rand -hex 24             # copy this: it's your database password
nano .env
```

In `nano`, fill in (arrow keys to move, paste with right-click or Ctrl+Shift+V):

```
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
TFNSW_API_KEY=your-tfnsw-token
OPENSKY_CLIENT_ID=your-client-id
OPENSKY_CLIENT_SECRET=your-client-secret
AISSTREAM_API_KEY=your-aisstream-key
DOMAIN=ozwatch-yourname.duckdns.org
DUCKDNS_SUBDOMAIN=ozwatch-yourname
DUCKDNS_TOKEN=your-duckdns-token
POSTGRES_PASSWORD=the-long-random-string-from-openssl
```

`COMPOSE_FILE` makes every `docker compose` command include the production
setup (Caddy + HTTPS), so you never need the longer `-f … -f …` form. Make sure
these lines have **no `#`** in front. Save with
`Ctrl+O`, `Enter`, then exit with `Ctrl+X`. Then start everything:

```bash
docker compose up -d --build
```

The first build takes ~5 minutes on the small VM. Keep DuckDNS pointed at
the VM even if its IP changes:

```bash
deploy/duckdns-update.sh && echo "DuckDNS OK"
(crontab -l 2>/dev/null; echo "*/5 * * * * $HOME/OzWatch/deploy/duckdns-update.sh") | crontab -
```

## 5. Check it

- Open `https://ozwatch-yourname.duckdns.org`. The first HTTPS certificate
  takes up to a minute.
- `https://ozwatch-yourname.duckdns.org/health` should list all five sources
  (`ok`, or `starting` for the first minute).
- Problems? `docker compose logs caddy` (certificate/domain issues) or
  `docker compose logs api` (data sources).

## 6. After two days: check the bill

☰ menu → **Billing** → **Reports**, group by **SKU**. Charges for the VM
and disk should be cancelled out by free-tier or trial credits. If you see a
non-zero line (e.g. "External IP" or "Network Internet Egress … to
Australia"), note the amount: it's either cents, or ~US$3.65/month for the
IP. Then decide whether to keep it, or move to a cheap Sydney VPS using
[deploy.md](deploy.md).

## Updating the site later

After pushing changes to GitHub, on the VM:

```bash
cd ~/OzWatch && git pull
docker compose up -d --build
```

## Stopping it

To stop everything and avoid any charge: **VM instances** → `ozwatch` →
**Delete**. Also delete the DuckDNS domain if you won't reuse it.
