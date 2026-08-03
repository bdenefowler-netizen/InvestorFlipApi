# InvestorFlip

Tarrant County real-estate investor app with an Expo frontend, FastAPI backend,
live listing feeds, tax-roll matching, deal scoring, and Quill analysis.

## Database

InvestorFlip uses PostgreSQL through `DATABASE_URL`. Flexible feed and property
records are stored as indexed JSONB in `properties`, `tax_roll`, `live_sync_log`,
`saved`, `ai_analysis`, `enrichment`, and `tax_history` tables.

The migration from MongoDB Atlas to PostgreSQL is complete. The normal runtime
requires only `DATABASE_URL` — no MongoDB configuration is needed.

User-facing APIs hide records marked as demo, seeded sample, or synthetic. They
remain in PostgreSQL until an operator explicitly removes them, so deploying a
data-quality change never destroys the old reference data.

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

CakeMLS is available as an address-based MLS enrichment provider through RapidAPI.
It uses the same RapidAPI account key and is enabled by default when that key exists:

```text
RAPIDAPI_KEY=<secret copied from RapidAPI.com>
RAPIDAPI_CAKEMLS_ENABLED=true
```

The same `RAPIDAPI_KEY` also powers `us-real-estate-listings` for live listings,
tax history, and `/location-suggest`. Location suggestions are used automatically
when the PropertyReach suggestion provider is unavailable.

Realtor Search agent-profile enrichment is also enabled by default. It only runs when a
property provider supplies a valid Realtor.com `/realestateagents/` profile URL:

```text
RAPIDAPI_REALTOR_SEARCH_ENABLED=true
```

Realty in US agent listings are enabled by default and use a listing provider's
`fulfillmentId` to populate the mobile app's “More listings by this agent” section:

```text
RAPIDAPI_REALTY_US_ENABLED=true
```

Set any of the three `RAPIDAPI_*_ENABLED` variables to `false` to disable that
specific enrichment provider. Listing providers are queried independently; a
failure or empty response from one does not erase results from the others.

### Provider route map

| Provider | Method and endpoint | InvestorFlip trigger |
| --- | --- | --- |
| OpenWeb Ninja Real-Time Real Estate | `GET /realtime-real-estate-data/zillow/search` | Live sync |
| OpenWeb Ninja Real-Time Zillow | `GET /realtime-zillow-data/search` | Every live sync |
| RapidAPI Real-Time Real Estate | `GET /search` with endpoint fallbacks | Every live sync |
| CakeMLS | `POST cakemls.p.rapidapi.com/api/mls/` | Property detail |
| Realtor Search | `GET realtor-search.p.rapidapi.com/agents/detail-url` | Detail with an agent URL |
| Realty in US | `GET realty-us.p.rapidapi.com/agents/v2/listings` | Detail with a fulfillment ID |
| US Real Estate Listings | `GET /for-sale`, `/location-suggest`, `/taxHistory` | Sync, search, and tax |
| US Real Estate Data 1 | `GET /properties/lookup`, `/properties/{zpid}` | Detail fallback |
| ForeclosureListingsUSA | Fort Worth page scraper | Every live sync |
| Apify | Recent successful actor datasets | Every live sync when configured |

The mobile **Add** tab can also send `.csv`, `.xls`, and `.xlsx` files to
`POST /api/intake/upload`, or a recognized property-page URL to
`POST /api/intake/link`. Both paths reject addressless records, merge existing
houses by address, and run the same county/detail enrichment pipeline.

`GET /api/live/status` reports the configured/enabled state and route mapping
without returning any API-key value.

Scores are explicitly preliminary. County appraisals and automated estimates are
recorded as screening benchmarks, not ARV. Owner equity stays unknown without a
mortgage balance, and ROI stays unknown until ARV, repairs, holding costs, and selling
costs are available.

## Production safety variables

Set these on the Railway **InvestorFlipApi** service before triggering imports or
Quill from the mobile app:

```text
INVESTORFLIP_ADMIN_KEY=<a new long random secret>
ENABLE_API_BACKGROUND_SYNC=false
BRIGHTDATA_TOKEN=<optional current Bright Data token>
APIFY_API_KEY=<optional Apify account token>
APIFY_ALLOWED_ACTOR_IDS=<comma-separated approved property actor IDs>
APIFY_ALLOWED_TASK_IDS=<comma-separated approved property task IDs>
COUNTY_TAD_RECORDS_PER_RUN=20000
TARRANT_FORECLOSURE_CSV=<optional path to a current, independently verified CSV>
CORS_ALLOWED_ORIGINS=<optional comma-separated Expo web origins; native Android does not need it>
```

Only one Apify allowlist variable is required. Account-wide Apify imports are blocked
unless `APIFY_IMPORT_ALL_RUNS=true` is set deliberately. Railway cron is the production
scheduler; leave `ENABLE_API_BACKGROUND_SYNC=false` to prevent duplicate work when the
API restarts or scales.

After setting `INVESTORFLIP_ADMIN_KEY`, enter that same value once in the mobile app's
**Settings → Private Operations** field. It is stored in the device's secure storage
and attached only to imports, paid-provider pulls, enrichment, and Quill operations.
Never put provider API keys directly in Expo configuration or frontend source code.

County records are stored separately from live listings. TAD and tax-roll records can
be searched in the **County** tab or exported without the former 50,000-row cutoff;
the CSV includes normalized columns plus the complete raw TAD/tax-roll JSON. County
facts enrich a listing only on an exact account match or one unambiguous address plus
ZIP/city match.

The repository's old 20-row foreclosure CSV is a development fixture and is never
imported in production. Existing rows carrying its old source label are hidden as
synthetic. A current Clerk/trustee-sale file can be uploaded through the app's **Add**
tab, or mounted on Railway and referenced with `TARRANT_FORECLOSURE_CSV`.
