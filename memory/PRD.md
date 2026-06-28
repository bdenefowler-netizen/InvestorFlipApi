# TarrantREI — Tarrant County Real Estate Investor App

## Overview
Mobile app pulling real Tarrant County tax roll + live foreclosure auction feeds
into a unified investor pipeline. Every record is auto-classified by owner type,
scored across 5 deal vectors, and exportable to CSV/Excel.

## Data Sources (live)
- **Tarrant County Master.dat** (2.0 GB, 742-byte fixed-width) — 4,222 records parsed
- **Tarrant County Rec.DAT** (3.5 GB receivables) — tax-delinquent accounts flagged
- **Foreclosure Finder API** (RapidAPI) — bulk-pulls 100+ Fort Worth foreclosures across
  auction.com, Fannie Mae, Freddie Mac, HUD, Redfin
- **US Real Estate Data API** (`us-real-estate-data1`, RapidAPI) — property lookup by
  address: zpid, lat/lng, zestimate, rent_zestimate, tax_assessed_value, MLS ID, agent
- **Real-Time Real Estate Data API** (fallback) — broader Fort Worth coverage + photos
- **US Real Estate Listings API** (RapidAPI) — multi-year tax history
- **Claude Sonnet 4.6** via Emergent Universal Key — investment narrative

## API Endpoints
- `GET /api/filters`, `GET /api/properties`, `GET /api/properties/{id}`
- `GET /api/properties/{id}/nearby`
- `POST /api/properties/{id}/ai-analysis` (Claude, cached)
- `POST /api/properties/{id}/enrich` (us-real-estate-data1 primary, real-time-real-estate-data fallback, cached)
- `GET /api/properties/{id}/tax-history` (cached)
- `POST /api/feeds/sync?only=...&limit=...`
- `GET /api/feeds/status`
- `POST /api/feeds/upload-csv` (multipart: file, feed_source, listing_type)
- `GET /api/export.csv?filter=...`  ·  `GET /api/export.xlsx?filter=...`
- `GET/POST/DELETE /api/saved`, `GET /api/saved/ids`, `GET /api/owners/classify`

## Ingestion Pipeline
Every feed listing flows through one pipeline:
1. **Cross-match** against Master.dat by address+zip — updates if found
2. **Net-new** records get full property doc with synthesized financials
3. **Auto-classify** owner (9 types)
4. **Auto-score** Investment / Wholesale / Flip / Rental / Risk
5. **Flag** investor_owned, cash_buyer, high_equity, out_of_state_owner, tax_delinquent

## Mobile App Features
- 17 horizontal sticky filter chips with live counts.
- Listings feed with search + saves + color-coded listing pills.
- Property profile: real photo, enriched beds/baths/sqft/year + lot size + appliances,
  Zestimate + Rent Zestimate + Tax Assessed + MLS ID, 5-score grid, Claude AI narrative,
  full tax-roll financials, multi-year tax history table, nearby foreclosures/investor
  purchases, save + contact CTAs.
- Settings: live feed status with per-feed sync + Sync All, CSV/Excel export.
- Saved tab.

## Repo Layout
- Backend: `/app/backend/server.py`, `/app/backend/importers/tarrant_taxroll.py`,
  `/app/backend/importers/feeds.py`
- Frontend: `/app/frontend/app/(tabs)/*.tsx`, `/app/frontend/app/property/[id].tsx`,
  `/app/frontend/src/components/*`, `/app/frontend/src/lib/api.ts`,
  `/app/frontend/src/lib/feeds.ts`, `/app/frontend/src/theme/tokens.ts`

## Known Limitations / Next
- 4,222 of 2.75M Master records loaded (preview pod disk-bound). Full import on deploy.
- `us-real-estate-data1` has thin Fort Worth coverage; falls back to `real-time-real-estate-data`.
- Geocoding ready (lat/lng stored when available) — map view not yet built.
