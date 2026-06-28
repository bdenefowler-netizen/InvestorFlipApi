# TarrantREI — Tarrant County Real Estate Investor App

## Overview
Mobile app that pulls **real Tarrant County tax roll data** plus live external feeds
(RealtyInUS, Xome, TX Foreclosure) into a unified investor pipeline. Every record is
auto-classified by owner type, scored across 5 deal vectors, and exportable to CSV/Excel.

## Data Sources
- **Tarrant County Master.dat** (2.0 GB, 742-byte fixed-width) — 4,222 real records parsed
- **Tarrant County Rec.DAT** (3.5 GB receivables) — 2,242 tax-delinquent accounts flagged
- **Real-Time Real Estate Data API** (RapidAPI) — beds/baths/sqft/year/photos enrichment
- **US Real Estate Listings API** (RapidAPI) — multi-year tax history per parcel
- **RealtyInUS feed** — scaffolded; activates when subscribed RapidAPI plan available
- **Xome REO feed** — scaffolded; activates when partner API key provided
- **TX Foreclosure feed** — CSV-driven, **20 sample records ingested**; weekly trustee
  sale CSV drop-in at `/app/backend/data/tx_foreclosures.csv` or POST to `/api/feeds/upload-csv`
- **Claude Sonnet 4.6** via Emergent Universal Key — investment narrative per property

## API Endpoints
- `GET /api/filters`, `GET /api/properties`, `GET /api/properties/{id}`
- `GET /api/properties/{id}/nearby` — same-ZIP foreclosures + investor purchases
- `POST /api/properties/{id}/ai-analysis` — Claude narrative (cached)
- `POST /api/properties/{id}/enrich` — RapidAPI listing data (cached)
- `GET /api/properties/{id}/tax-history` — multi-year tax history (cached)
- `POST /api/feeds/sync?only=...&limit=...` — pull from all/single feed and ingest
- `GET /api/feeds/status` — per-feed property counts
- `POST /api/feeds/upload-csv` (multipart: file, feed_source, listing_type)
- `GET /api/export.csv?filter=...` — 34-column CSV export
- `GET /api/export.xlsx?filter=...` — Excel export with same schema
- `GET/POST/DELETE /api/saved`, `GET /api/saved/ids`, `GET /api/owners/classify`

## Ingestion Pipeline
Every feed listing flows through one pipeline:
1. **Cross-match** against Master.dat by address+zip → if found, update with listing info
2. **Net-new** records get full property doc with synthesized financials
3. **Auto-classify** owner (9 types incl. Law Firm via suffix+keyword+known-firm list)
4. **Auto-score** Investment / Wholesale / Flip / Rental / Risk
5. **Flag** investor_owned, cash_buyer, high_equity, out_of_state_owner, tax_delinquent

## Owner Intelligence (9 types)
Individual · LLC (incl. truncated " LL") · Corporation · Trust · Bank · Government ·
Nonprofit · Law Firm (suffix + keyword + known-firm list) · Attorney.

## Mobile App Features
- 17 horizontal sticky filter chips with live counts (REO, Foreclosure, As-Is,
  Investor, Cash House, High Equity, Cash Buyer, LLC, Law Firm, Tax Delinquent,
  Out-of-State, Vacant, Corporate, Trust-Owned, Bank-Owned, +).
- Listings feed: search address/owner/city/ZIP, save bookmarks, color-coded listing pills.
- Property profile: real photo (Street View), enriched beds/baths/sqft/year, 5-score grid,
  Claude AI narrative, full tax-roll financials, **multi-year tax history table**,
  nearby foreclosures + investor purchases, save + contact CTAs.
- **Settings screen**: live feed status with per-feed sync buttons, Sync All, CSV/Excel
  export buttons.
- Saved tab.

## Repo Layout
- Backend: `/app/backend/server.py`, `/app/backend/importers/tarrant_taxroll.py`,
  `/app/backend/importers/feeds.py`
- Frontend: `/app/frontend/app/(tabs)/*.tsx`, `/app/frontend/app/property/[id].tsx`,
  `/app/frontend/src/components/*`, `/app/frontend/src/lib/api.ts`,
  `/app/frontend/src/lib/feeds.ts`, `/app/frontend/src/theme/tokens.ts`
- Data: `/app/backend/data/big_a` (Rec.DAT), `big_b` (Master.dat), `tx_foreclosures.csv`

## Known Limitations / Next
- 4,222 of 2.75M Master records loaded in this preview pod (disk-bound). Full import on deploy.
- RealtyInUS endpoint scaffold (need separate paid RapidAPI subscription).
- Xome public API returned 404 — need affiliate/partner key or scraper authorization.
- TX Foreclosure feed reads CSV; integrate official PDF parser for tarrantcounty.gov weekly notices.
