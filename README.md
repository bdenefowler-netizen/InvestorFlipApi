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

See `memory/PRD.md` for the product and API overview.

## Automatic Tarrant tax-roll sync

The `tax-roll-sync` Railway cron service checks the official Tarrant County tax-roll
page at 15:00 UTC every weekday. It discovers the newest dated ZIP, skips files that
were already applied successfully, and enriches only live properties explicitly
located in Fort Worth, Texas. The full county ZIP is scanned because `MASTER.DAT`
does not include a reliable property-city field; unmatched county records are never
written to InvestorFlip.
