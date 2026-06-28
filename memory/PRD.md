# TarrantREI — Tarrant County Real Estate Investor App

## Overview
Mobile app that pulls **real Tarrant County tax roll data** and surfaces investor-ready
deals (REO, As-Is, Investor, Cash House, Foreclosure) with automatic owner classification,
AI deal scoring, and Claude-generated investment analysis. Each property is live-enriched
with beds/baths/sqft/year-built/photos from the Real-Time Real Estate Data API plus
multi-year tax history from the US Real Estate Listings API.

## Data Sources (live)
- **Tarrant County Master.dat** (2.0 GB, 742-byte fixed-width) — 4,222 real records parsed
- **Tarrant County Rec.DAT** (3.5 GB receivables) — 2,242 tax-delinquent accounts flagged
- **Real-Time Real Estate Data API** (RapidAPI) — beds/baths/sqft/year built/photos/list price
- **US Real Estate Listings API** (RapidAPI) — multi-year tax history per parcel
- **Claude Sonnet 4.6** via Emergent Universal Key — per-property investment narrative

## API Endpoints
- `GET /api/filters`, `GET /api/properties`, `GET /api/properties/{id}`
- `GET /api/properties/{id}/nearby` — same-ZIP foreclosures + investor purchases
- `POST /api/properties/{id}/ai-analysis` — Claude narrative (cached)
- `POST /api/properties/{id}/enrich` — beds/baths/sqft/photos/list-price (cached)
- `GET /api/properties/{id}/tax-history` — 6+ years tax/assessment (cached)
- `GET/POST/DELETE /api/saved` + `GET /api/saved/ids`
- `GET /api/owners/classify?name=...`

## Owner Intelligence
Classifier: Individual · LLC (incl. truncated " LL") · Corporation · Trust · Bank ·
Government · Nonprofit · Law Firm (suffix + keyword + known-firm) · Attorney.

## Features
- 17 investor filter chips with live counts (REO, Foreclosure, As-Is, Investor, Cash House,
  High Equity, Cash Buyer, LLC, Law Firm, Tax Delinquent, Out-of-State, Vacant, Corporate,
  Trust-Owned, Bank-Owned, etc.) — horizontal scroll, sticky header.
- Listings feed: search by address/owner/city/ZIP, save bookmarks, color-coded listing pills.
- Property profile: real photo (Google Street View via RapidAPI), enriched quick-stats
  (beds/baths/sqft/built), owner-intelligence card, 5-score AI grid, AI narrative analysis,
  full financials + tax-roll fields, **multi-year tax history table**, nearby
  foreclosures/investor purchases, save + contact-owner CTAs.
- Saved tab + Settings (lists every active data source).

## Repo Layout
- Backend: `/app/backend/server.py`, `/app/backend/importers/tarrant_taxroll.py`
- Frontend: `/app/frontend/app/(tabs)/*.tsx`, `/app/frontend/app/property/[id].tsx`,
  `/app/frontend/src/components/*`, `/app/frontend/src/lib/api.ts`,
  `/app/frontend/src/theme/tokens.ts`

## Known Limitations / Next
- 4,222 of 2.75M Master records loaded in this preview pod (disk-bound). Full import after deploy.
- Master.dat carries owner mailing addr only — situs city/zip is inferred (mostly Fort Worth).
- Tax history needs Realtor.com property_id; we use Zillow zpid as best-effort, ~50% hit rate.
- "Nearby" matches by ZIP. Geocoded lat/lng + map view planned next.
