# InvestorFlip

Tarrant County real-estate investor app with an Expo frontend, FastAPI backend,
live listing feeds, tax-roll matching, deal scoring, and Quill analysis.

## Database

InvestorFlip uses PostgreSQL through `DATABASE_URL`. Flexible feed and property
records are stored as indexed JSONB in `properties`, `tax_roll`, `live_sync_log`,
`saved`, `ai_analysis`, `enrichment`, and `tax_history` tables.

To copy the existing Atlas database without changing or deleting its source data:

```bash
cd backend
pip install -r requirements-migration.txt
MONGO_URL='mongodb+srv://...' DB_NAME='tarrantrei' DATABASE_URL='postgresql://...' \
  python migrate_mongo_to_postgres.py
```

After verifying PostgreSQL counts and API behavior, remove `MONGO_URL` and `DB_NAME`
from the deployed application. The normal runtime requires only `DATABASE_URL`.

User-facing APIs hide records marked as demo, seeded sample, or synthetic. They
remain in PostgreSQL until an operator explicitly removes them, so deploying a
data-quality change never destroys the old reference data.

The main property feed is intentionally narrower than the provider inventory.
InvestorFlip only returns houses with explicit evidence for at least one target:
motivated-seller language, foreclosure/short sale, distressed-condition language,
REO/bank ownership, county tax delinquency, cash-offer language, investor-special
language, or an as-is sale. Structured provider flags and county balances outrank
marketing text. FSBO, absentee, LLC, and out-of-state ownership do not prove seller
motivation and do not qualify a listing by themselves.

See `memory/PRD.md` for the product and API overview.

## Automatic Tarrant tax-roll sync

The Railway data-sync cron checks the official Tarrant County tax-roll page at
15:00 UTC daily. It discovers the newest dated ZIP, skips files that
were already applied successfully, and enriches only live properties explicitly
located in Fort Worth, Texas. The full county ZIP is scanned because `MASTER.DAT`
does not include a reliable property-city field; unmatched county records are never
written to InvestorFlip.

## Automatic live-listing sync

The Railway cron command can refresh Fort Worth residential listings before checking
the tax roll. A successful listing refresh normalizes nested provider fields,
preserves higher-trust county owner/tax facts, and resets each listing's missed-sync
counter. A listing is marked stale only after two successful provider responses omit
it; an empty or failed provider response never retires existing listings.
Each run takes the first usable OpenWeb listing source and the first usable RapidAPI
listing source, then merges and deduplicates their results. This keeps both provider
families active without calling every fallback endpoint after a successful response.
Set `ENABLE_LIVE_LISTING_CRON=true` only after confirming the provider plan's quota;
the cron skips listing requests by default so it cannot unexpectedly consume credits.

The preferred direct providers are OpenWeb Ninja's unified Real-Time Real Estate
Zillow API and dedicated Real-Time Zillow API. Configure these Railway variables:

```text
OPENWEB_NINJA_REAL_ESTATE_API_KEY=<key for Real-Time Real Estate Data>
OPENWEB_NINJA_ZILLOW_API_KEY=<key for Real-Time Zillow Data>
```

Both direct providers use `X-API-Key`; their base URLs and paths are built in. Do not
set an auth scheme or put either direct API key in `RAPIDAPI_KEY`. `RAPIDAPI_KEY` is
a separate optional fallback for subscriptions purchased through RapidAPI and for
the PropertyReach address-suggestion integration. Legacy `OPENWEB_NINJA_API_KEY` and
`OPENWEB_NINJA_KEY` variables remain supported as fallbacks, but the explicit names
above prevent two product keys from being mixed up.

CakeMLS is available as an address-based MLS enrichment fallback through RapidAPI.
It uses the same RapidAPI account key and is opt-in so opening property details does
not consume CakeMLS credits unexpectedly:

```text
RAPIDAPI_KEY=<secret copied from RapidAPI.com>
RAPIDAPI_CAKEMLS_ENABLED=true
```

The same `RAPIDAPI_KEY` also powers `us-real-estate-listings` for live listings,
tax history, and `/location-suggest`. Location suggestions are used automatically
when the PropertyReach suggestion provider is unavailable.

Realtor Search agent-profile enrichment is also opt-in. It only runs when a
property provider supplies a valid Realtor.com `/realestateagents/` profile URL:

```text
RAPIDAPI_REALTOR_SEARCH_ENABLED=true
```

Realty in US agent listings are opt-in and use a listing provider's
`fulfillmentId` to populate the mobile app's “More listings by this agent” section:

```text
RAPIDAPI_REALTY_US_ENABLED=true
```

### Provider route map

| Provider | Method and endpoint | InvestorFlip trigger |
| --- | --- | --- |
| OpenWeb Ninja Real-Time Real Estate | `GET /realtime-real-estate-data/zillow/search` | Preferred live sync and detail |
| OpenWeb Ninja Real-Time Zillow | `GET /realtime-zillow-data/search` | Live-sync and detail fallback |
| CakeMLS | `POST cakemls.p.rapidapi.com/api/mls/` | Property detail |
| Realtor Search | `GET realtor-search.p.rapidapi.com/agents/detail-url` | Detail with an agent URL |
| Realty in US | `GET realty-us.p.rapidapi.com/agents/v2/listings` | Detail with a fulfillment ID |
| US Real Estate Listings | `GET /for-sale`, `/location-suggest`, `/taxHistory` | Sync, search, and tax |
| US Real Estate Data 1 | `GET /properties/lookup`, `/properties/{zpid}` | Detail fallback |

`GET /api/live/status` reports the configured/enabled state and route mapping
without returning any API-key value.

Scores are explicitly preliminary. County appraisals and automated estimates are
recorded as screening benchmarks, not ARV. Owner equity stays unknown without a
mortgage balance, and ROI stays unknown until ARV, repairs, holding costs, and selling
costs are available.
