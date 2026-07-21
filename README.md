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
the tax roll. A successful listing refresh normalizes nested Realtor/NTREIS fields,
preserves higher-trust county owner/tax facts, and resets each listing's missed-sync
counter. A listing is marked stale only after two successful provider responses omit
it; an empty or failed provider response never retires existing listings.
Set `ENABLE_LIVE_LISTING_CRON=true` only after confirming the RapidAPI plan's quota;
the cron skips listing requests by default so it cannot unexpectedly consume credits.

Scores are explicitly preliminary. County appraisals and automated estimates are
recorded as screening benchmarks, not ARV. Owner equity stays unknown without a
mortgage balance, and ROI stays unknown until ARV, repairs, holding costs, and selling
costs are available.
