# Metropole Logistics — build guide

This folder is a complete, working starter for the Metropole Logistics platform:

| Folder | What it is |
|---|---|
| `web/` | The website: the map, alerts, countries, routes, forecasts and briefings. |
| `supabase/` | The database setup (`schema.sql`) and starting data (`seed.sql`). |
| `collector/` | The program that collects news, tags it with AI, saves events and sends email alerts. |
| `.github/workflows/` | Instructions that make GitHub run the collector automatically every hour, for free. |

It works in stages. Each stage gives you something that runs, so you can stop at any point and still have a working site.

**How it fits together**

```
News sources ──► Collector (runs hourly on GitHub) ──► Database (Supabase) ──► Website (Netlify)
 GDELT, ReliefWeb,   filters, tags with Claude,          events, mines,            reads the database,
 RSS feeds,          merges duplicates,                  countries, subscribers    shows the map
 OFAC sanctions      emails alerts (Resend)
```

You'll make five free accounts along the way: **GitHub**, **Supabase**, **Netlify**, **Anthropic** and **Resend**.

---

## Stage 0 — Run it on your own computer (about 20 minutes, no accounts)

1. **Install Python 3.12.** Download it from python.org. On Windows, tick "Add Python to PATH" in the installer.
2. **Install a code editor** (optional but helpful): Visual Studio Code from code.visualstudio.com.
3. **Unzip this folder** somewhere easy, such as `Documents/metropole-platform`.
4. **Open a terminal in the folder.**
   - Mac: open Terminal, type `cd ` (with a space), drag the folder into the window, press Enter. Type `ls`: you should see `README.md`, `requirements.txt`, `collector` and `web`.
   - Windows: open the folder, click the address bar, type `cmd`, press Enter.
5. **Create a private Python environment and install the two packages:**
   - Mac:
     ```
     python3 -m venv .venv
     source .venv/bin/activate
     pip install -r requirements.txt
     ```
     Your prompt now starts with `(.venv)`. Every time you open a new Terminal window later, `cd` into the folder and run `source .venv/bin/activate` first.
   - Windows:
     ```
     py -m venv .venv
     .venv\Scripts\activate
     pip install -r requirements.txt
     ```
   On a Mac, if `python3` is not found, install Python from python.org, then run **Install Certificates.command** from Applications → Python 3.x, and reopen Terminal.
6. **Run the collector on sample data:**
   ```
   python -m collector run --sample
   ```
   You should see it read 12 sample items, throw out the irrelevant ones, merge a duplicate and create 7 events.
   It saves them to a `local_db` folder. Emails it would have sent are saved in `local_db/outbox` so you can open them in a browser.
7. **Open the website:** double-click `web/index.html`. It shows the built-in snapshot data. The top-right badge says **Snapshot**.

You now have the whole system working on your machine with sample data.

---

## Stage 1 — Create the database (about 15 minutes)

1. Go to **supabase.com**, sign up, and click **New project**. Pick a region close to you (e.g. London or Frankfurt). Save the database password somewhere safe.
2. When the project is ready, open **SQL Editor** (left sidebar) → **New query**.
3. Open `supabase/schema.sql` in your editor, copy everything, paste it into Supabase, click **Run**. You should see "Success".
4. Do the same with `supabase/seed.sql`. This loads the 26 countries, 52 mines and plants, 25 ports, 12 corridors, events, forecasts and the first briefing.
5. Open **Table Editor** to see your data. You can edit any cell here, like a spreadsheet.

**Find your keys:** Project Settings → API. You need three things:
- **Project URL** (looks like `https://abcd1234.supabase.co`)
- **anon / public key** — safe to put in the website
- **service_role key** — secret; only for the collector. Never put it in `web/` or share it.

---

## Stage 2 — Connect the collector to the database (10 minutes)

1. Copy `.env.example` to a new file named `.env` (same folder).
2. Fill in `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (the service_role key).
3. Check the connection:
   ```
   python -m collector status
   ```
   It should say `Database: SupabaseDB` and list your table counts.
4. Run it for real (this goes to the internet):
   ```
   python -m collector run
   ```
   It pulls the last 6 hours of news from GDELT. Without an AI key it uses a free keyword tagger, which sends **everything** to your review queue. That's fine to start.

---

## Stage 3 — Put the website online (15 minutes)

1. Open `web/config.js` and fill in `SUPABASE_URL` and `SUPABASE_ANON_KEY` with the **publishable** key (`sb_publishable_...`, or the legacy **anon** key). Never the secret key.
2. Go to **app.netlify.com/drop** and drag the whole `web` folder onto the page.
3. Netlify gives you a link like `https://something.netlify.app`. Open it: the badge now says **Live**, and the map reads from your database.
4. Optional: sign up for a Netlify account to keep the site, rename it, or connect your own domain (Site settings → Domain management).

Every time you change the website files, drag the `web` folder onto Netlify again (or connect Netlify to your GitHub repo so it updates automatically).

---

## Stage 4 — Make it run by itself every hour (20 minutes)

1. Sign up at **github.com** and create a **New repository**. Name it `metropole-platform` and choose **Private**.
2. Upload this folder: on the new repo page, click **uploading an existing file**, drag in everything **except** `.env` and `local_db`, and commit.
   (The `.github` folder is hidden on Mac. Press Cmd+Shift+. in Finder to show it, and make sure it's uploaded.)
3. Add your secrets: repo → **Settings → Secrets and variables → Actions → New repository secret**. Add each of these with the same values as your `.env`:
   `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `ALERT_FROM`, `ADMIN_EMAIL`, `SITE_URL`, `RELIEFWEB_APPNAME`.
   Leave out any you don't have yet.
4. Go to the **Actions** tab, click **Collect and alert**, then **Run workflow**. Wait a minute and click the run to see its log.

From now on it runs every hour, and the digest goes out daily at 06:13 UTC. Sanctions screening runs on Mondays.

---

## Stage 5 — Turn on AI tagging and email (20 minutes)

**AI tagging (Claude):**
1. Go to **console.anthropic.com**, add billing, and create an API key.
2. Put it in `.env` and in GitHub Secrets as `ANTHROPIC_API_KEY`.
3. The collector now uses Claude to tag each relevant item: country, the exact mine or corridor, metals, event type, severity (1–4), a clean title and a two-sentence summary. Only items that mention a place you track **and** a mining or logistics term are sent to Claude, which keeps cost down.

**Email (Resend):**
1. Sign up at **resend.com** and add your domain (they walk you through the DNS records). Until your domain is verified, you can only send to your own address.
2. Create an API key; add `RESEND_API_KEY`, `ALERT_FROM` (e.g. `Metropole Alerts <alerts@yourdomain.com>`) and `ADMIN_EMAIL` (you) to `.env` and GitHub Secrets.
3. Subscribe yourself through the form on the website's Briefings tab.

**ReliefWeb (optional):** request an app name at apidoc.reliefweb.int. When they approve it, add `RELIEFWEB_APPNAME`.

---

## Your daily routine as the analyst

- **Review events.** You'll get an email when events need review. In Supabase: Table Editor → `events` → filter `status` = `pending`. Change `status` to `approved` or `rejected`. Approved events appear on the site and go out as alerts on the next hourly run.
  - Low-severity events the AI is confident about are approved automatically (`auto`). Change the thresholds with `REVIEW_FROM_SEVERITY` and `REVIEW_BELOW_CONFIDENCE`.
- **Fix the register.** Edit mines, ports and corridors in Table Editor. Add other names the news uses for a mine in the `aliases` column (e.g. `{Kakula,Kamoa}`); this improves matching.
- **Update risk scores.** Edit the five component columns in `countries`; the overall score recalculates itself.
- **Publish a briefing.** Insert a row in `briefings`: `id` (e.g. `001`), date, title, lede, and `sections` as `[["Heading","Paragraph"],["Heading","Paragraph"]]`.
- **Resolve forecasts.** When a question resolves, set `outcome` to true or false. The Forecasts tab updates your Brier score automatically.
- **Add sources.** Paste RSS feed URLs into `collector/feeds.txt` (company announcements, exchange news, Google News searches). Add counterparties to screen into `collector/watch_names.txt`.

---

## Monthly cost

| Item | Free tier | When you outgrow it |
|---|---|---|
| Supabase (database) | Free (pauses after a week of no activity) | $25/mo Pro |
| Netlify (website) | Free | $19/mo |
| GitHub Actions (hourly runs) | Free within 2,000 min/mo on private repos | small overage or public repo |
| Claude API (tagging) | — | roughly $10–100/mo depending on volume and model |
| Resend (email) | Free for 3,000 emails/mo | $20/mo |
| Domain | — | about $15/year |

Expect **$0–50 a month** to start, rising with usage. Prices change; check each provider's page.

---

## Commands

```
python -m collector run              # collect, tag, store, alert
python -m collector run --sample     # same, with built-in sample data (no internet)
python -m collector run --no-email   # don't send emails this time
python -m collector digest           # send the daily digest
python -m collector sanctions        # screen operators and watch-listed vessels against the OFAC list
python -m collector prices           # pull daily benchmark prices from the US EIA
python -m collector status           # what's in the database
python tools/build_seed.py           # rebuild seed.sql after editing supabase/seed_data.json
```

---

## Troubleshooting

- **`python` not found:** activate the environment first (`source .venv/bin/activate` on Mac). Outside it, use `python3` (Mac) or `py` (Windows).
- **Website badge still says Snapshot:** check `web/config.js` has the URL and **anon** key, re-upload to Netlify, and refresh. Open the browser console (F12) to see the error.
- **"permission denied" from Supabase:** the collector must use the **service_role** key; the website must use the **anon** key.
- **GitHub run fails:** open the run in the Actions tab; the log says which step failed. Most often a secret is missing or misspelled.
- **No events after a run:** normal on quiet hours. Try `LOOKBACK_HOURS=48` in `.env` and run again locally.
- **Supabase project paused:** free projects pause after a week of inactivity; the hourly collector keeps it awake once Stage 4 is done.

---

## Adding the energy sector and more data (update from November 2026)

The engine now covers two sectors, **Metals** and **Energy**, with a switch in the top bar. Energy adds oil and gas fields, refineries, LNG plants and export terminals across Africa and the Mediterranean, pipelines (EACOP, Chad–Cameroon, Transmed, Medgaz, Greenstream, SUMED, Niger Delta, Greater Nile, Niger–Benin), and the sea lanes tankers use (Hormuz, Red Sea–Suez, Cape, Gibraltar, Gulf of Guinea), plus ten new countries.

### If you already have a working database (upgrade in 10 minutes)

1. In Supabase → SQL Editor, run `supabase/migrate_energy.sql` once. It adds the new columns and a `prices` table and keeps everything you have.
2. Run the new `supabase/seed.sql`. It adds the energy register and events. Your own events, approvals and subscribers are untouched.
3. Replace the `collector` and `tools` folders on your computer with the new ones (keep your `.env`).
4. Re-upload the new `web` folder to Netlify after filling in `web/config.js` again.
5. In GitHub, upload the same new folders and workflow files so the hourly run uses them.

A fresh install needs no migration: `schema.sql` already includes everything.

### New commands

```
python -m collector prices           # daily Brent, WTI, diesel, gasoline, Henry Hub from the US EIA (free key: eia.gov/opendata → EIA_API_KEY in .env)
python -m collector sanctions        # now also screens vessels listed in collector/watch_vessels.txt
python tools/import_jodi.py file.csv --push   # oil demand from JODI (see the update-2 section below)
python relay/ais_relay.py            # AIS relay for the vessel tracker (see the update-2 section below)
```
Prices show on the site's wire ticker once stored. The daily GitHub workflow runs `prices` after the digest if `EIA_API_KEY` is in the repo secrets.

### Loading the bigger public datasets

Each importer reads a file you download, writes a JSON file to `register/` for you to check, and pushes to the database with `--push`. Run them from the project folder with the environment active.

| Dataset | Get it from | Command |
|---|---|---|
| Global Energy Monitor trackers (fields, pipelines, LNG, refineries) | globalenergymonitor.org/projects → download the tracker spreadsheet (free, short form) | `python tools/import_gem.py tracker.xlsx --kind auto --push` |
| World Port Index (every port, with coordinates) | msi.nga.mil/Publications/WPI → CSV download | `python tools/import_wpi.py UpdatedPub150.csv --push` |
| Real pipeline / railway paths from OpenStreetMap | no download needed | `python tools/fetch_osm_corridors.py --id eacop --name "EACOP" --type pipeline --push` |
| Any register file you edited by hand | — | `python tools/push_register.py assets register/my_assets.json` |

Notes:
- GEM spreadsheets need `pip install openpyxl` once.
- Imported rows get ids starting `gem-` or `wpi-`, so they sit beside the desk's own entries and can be re-imported without duplicates. Delete any you don't want in Table Editor.
- OpenStreetMap coverage varies; check the drawn path on the map before trusting it.
- JODI (monthly production and stocks) and EITI (ownership and payments) are reference sources for country profiles; download their CSVs when you write a profile rather than importing them.
- Attribution: GEM data is CC BY 4.0; OpenStreetMap is ODbL. Both ask to be credited on the Sources tab, which the site already does.

### Energy in the client workspace

Positions can now be in tonnes, barrels (`bbl`, `kb`, `mbbl`), m3 or MMBtu. Spreadsheet uploads accept a `unit` column, and pasted lines like `950 kb Bonny Light ex Bonga via Cape to Refiner F` are read with the unit. Exposure totals are shown per unit.

---

## South America, oil demand and the vessel tracker (update 2, November 2026)

Three additions. A **Region** switch (Africa / Americas / All) next to the sector switch: South America brings 12 jurisdictions (Brazil to Trinidad), the Andean copper and lithium belt, Brazilian iron ore, Vaca Muerta, the Guyana–Suriname basin, Venezuela, the Panama Canal and Magellan lanes, 30 events and 7 forecasts. A **Demand** tab and map layer tracking total oil consumption of the 30 largest consumers, with a note on how each market touches your flows. A **Vessels** tab and map layer: cargo and tanker positions from AIS, classified by type and size, screened against sanctions lists, flagged when dark, near a chokepoint or inside an instability zone, with filters for all of it.

### Upgrade an existing database (10 minutes)

1. Supabase → SQL Editor: run `supabase/migrate_v3.sql` once (adds `region` columns, the `demand` and `vessels` tables; keeps everything you have).
2. Run the new `supabase/seed.sql`. It adds the South American register, events and forecasts, and a demand snapshot. Your own events, approvals and subscribers are untouched.
3. Replace the `collector`, `tools` and `relay` folders on your computer with the new ones (keep your `.env`), then `pip install -r requirements.txt` again inside the environment (adds `websockets`).
4. Re-upload the new `web` folder to Netlify after filling in `web/config.js` again (the publishable key only).
5. Upload the same folders to GitHub so the hourly run uses the new collector (it now watches South American feeds and tags those countries).

A fresh install needs no migration: `schema.sql` includes everything.

### Oil demand: replacing the snapshot with JODI

The site ships with a desk snapshot (approximate yearly figures). To load real monthly data:

1. Download the free JODI World Oil Database CSV: jodidata.org → Oil → Data downloads → "World Oil Database" (primary + secondary, kb/d).
2. `python tools/import_jodi.py ~/Downloads/world_Oil_Database.csv` — prints yearly averages so you can eyeball them, writes `register/jodi_demand.json`.
3. `python tools/import_jodi.py ~/Downloads/world_Oil_Database.csv --push` — replaces the snapshot rows. The site switches its source label to JODI automatically. Repeat monthly (JODI updates around the 20th).

Other products: `--product GASDIES` (diesel), `GASOLINE`, `JETKERO`, `LPG`, `RESFUEL`. Rows are kept per product, so you can load several; the site shows `total`.

### Vessel tracker: running the AIS relay

AIS positions come from aisstream.io (free, no card). Browsers cannot connect to it directly, so a small relay keeps the connection open and writes positions to your database every minute. You run it anywhere Python runs.

1. Get a key: aisstream.io → sign in with GitHub → "Create API key". Add it to `.env` as `AISSTREAM_API_KEY=...`.
2. Test without a key first: `python relay/ais_relay.py --sample` replays a few recorded messages into the local test database and prints how they were classified.
3. Run it for real: `python relay/ais_relay.py`. Within a minute or two the Vessels tab on your site shows ships in the boxes listed in `relay/boxes.json` (Africa and the Mediterranean, the Gulf and Red Sea, South America, the Caribbean and Panama). Leave the terminal open; press Ctrl-C to stop.
4. Keep it running when your Mac is asleep by putting it on a small server. Easiest paths, in order:
   - **Railway or Render** (about US$5/month): new project from your GitHub repo, start command `python relay/ais_relay.py`, add `AISSTREAM_API_KEY`, `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` as environment variables. The `relay/Procfile` is there for platforms that use one.
   - **Any VPS** (Hetzner, DigitalOcean, US$4–6/month): clone the repo, create the `.env`, run under `systemd` or `tmux`.
   - A spare Mac or Raspberry Pi at home works too.
5. Once a week: `python relay/ais_relay.py --prune` removes ships not heard from in 14 days (or run it from the daily GitHub workflow).

What the relay does with each ship, so you can explain it to a client:
- **Type and class** come from the AIS ship-type code and the ship's dimensions: tankers are sized VLCC / Suezmax / Aframax / Panamax / MR / Handy by length; cargo ships become Valemax / Capesize / Panamax / Supramax / Handysize bulkers, or container ships when the name or proportions say so. LNG and LPG are recognised from the name. These are estimates; the Vessels tab says so.
- **Laden or ballast** is estimated from the reported draught against the class's typical loaded draught.
- **Flag** is read from the first three digits of the MMSI.
- **Sanctions** matching compares the ship's name with the vessel entries in the OFAC SDN list (downloaded on start and every 24 hours) and with `collector/watch_vessels.txt`. A match is a lead to check in Equasis or GISIS, not a finding.
- **Dark** means no AIS message for 12 hours or more. **Near chokepoint** is within 150 km of a strait or border in the register; **in zone** means inside an instability polygon.
- Ownership is not broadcast on AIS. The detail panel links to Equasis (free registration) where registered owners are listed; the workspace can pin a counterparty to a vessel.

Settings in `.env`: `RELAY_TYPES=70-89` (cargo and tankers; `all` keeps everything), `RELAY_MIN_LENGTH=90` metres, `RELAY_FLUSH_SECONDS=60`, `RELAY_PRUNE_DAYS=14`. Edit `relay/boxes.json` to change the areas (fewer, larger boxes are fine).

Cost: aisstream.io is free for this use; the relay adds about US$5/month if hosted. Supabase's free tier handles a few thousand ships updating every minute; if you widen the boxes to the whole world, move to the US$25 plan.

### Vessels in the client workspace

Position rows can carry a `vessel` column (name, IMO or MMSI), and pasted lines like `950 kb Bonny Light ex Bonga on SAVANNAH TIDE via Cape` pick the vessel up. The Exposure tab gains a Fleet card showing each nominated vessel's class, destination, last position and any sanctions, dark, zone or chokepoint flags, and alerts near a nominated vessel count as touching your book.

---

## What to build next

1. **Deepen one segment.** Pick Copperbelt copper and cobalt and fill every mine's owners, output, offtakers and aliases. Depth is what clients pay for.
2. **Add better sources.** Company announcement feeds and ministry notices; later, ACLED (commercial licence) for conflict events.
3. **An admin page** for reviewing events in one click instead of Table Editor.
4. **Client logins and watchlists** using Supabase Auth, so each client picks their countries, metals and routes.
5. **Payments** with Stripe once pilots convert.

Security reminders: keep the repo private, never commit `.env`, and never put the service_role key in the website.

---

*Data note:* the starting dataset was compiled from public reporting to mid-2026. Items marked "verify" carry known uncertainty. Check facts before relying on them. This service provides information only, not investment advice.
