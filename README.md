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
