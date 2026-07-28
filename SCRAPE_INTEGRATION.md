# Free Real Estate Data Integration Guide

## Overview

This integration uses **ONLY free public data sources** - no subscriptions required.

## Free Data Sources

| Source | Cost | Method | Data |
|--------|------|--------|------|
| Fort Worth Code Violations | ✅ FREE | ArcGIS API | 600+ distressed properties |
| Tarrant County Foreclosures | ✅ FREE | CSV file | 20+ foreclosure records |
| TAD (Tarrant Appraisal District) | ✅ FREE | ArcGIS API | Property ownership, values |
| New Western Marketplace | ✅ FREE | Web scraping | Wholesale deals |
| Stessa Marketplace | ✅ FREE | Web scraping | Investment properties |
| SmartPropLeads | ✅ FREE | Web scraping | 3M+ DFW leads, AI-scored |
| Free Skip Tracing | ✅ FREE | Public records | Owner contact info |

## What Was Removed (Subscriptions Required)

❌ **PropStream** - Requires paid subscription
❌ **BatchLeads** - Requires paid API key

## SmartPropLeads - FREE DFW Leads

SmartPropLeads is a DFW-focused lead platform with:
- **3M+ parcels** across 11 North Texas counties
- **14 lead types** with AI scoring (Hot/Warm)
- **FREE to browse** - no subscription needed
- **County records verified** - data from public appraisal records

### Lead Types Available

| Lead Type | Count | Hot | Warm |
|-----------|-------|-----|------|
| Absentee Owners | 543,073 | 11,733 | 59,438 |
| Out-of-State Owners | 45,811 | 3,813 | 12,612 |
| Non-Owner Occupied | 1.1M | 27,162 | 56,964 |
| Long-Term Owners (15y+) | 315,488 | 14,177 | 48,543 |
| Senior / OV65 Owners | 233,138 | 5,687 | 45,478 |
| Vacant Lots | 187,302 | 2,305 | 6,290 |
| New Construction | 117,842 | 287 | 436 |
| Recent Transfers | 285,134 | 2,570 | 2,490 |
| Pre-Foreclosure | 846 | 843 | 0 |
| Tax Delinquent | 79,498 | 42,994 | 21,167 |
| High Equity Owners | 453,132 | 9,628 | 49,316 |
| Cash / Investor Buyers | 196,158 | 1,580 | 4,578 |
| Free & Clear (No Mortgage) | 45,406 | 190 | 760 |
| Commercial | 133,936 | 2,460 | 8,349 |

### Counties Covered

- Collin County
- Dallas County
- Denton County
- **Tarrant County** ← Your focus
- Rockwall County
- Kaufman County
- Ellis County
- Johnson County
- Parker County
- Wise County
- Hunt County

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/import/fort-worth-violations` | POST | Import code violations |
| `/api/import/foreclosures` | POST | Import foreclosures |
| `/api/import/tad` | POST | Import TAD property data |
| `/api/import/new-western` | POST | Import New Western deals |
| `/api/import/stessa` | POST | Import Stessa properties |
| `/api/import/smartpropleads` | POST | Import SmartPropLeads |
| `/api/import/all` | POST | Import all FREE sources |
| `/api/distressed-properties` | GET | Query distressed properties |
| `/api/skip-trace` | GET | Free skip tracing |
| `/api/tad/search` | GET | Search TAD by address/owner |
| `/api/smartpropleads/lead-types` | GET | List SmartPropLeads lead types |
| `/api/smartpropleads/counties` | GET | List counties covered |
| `/api/data-sources/status` | GET | Check source availability |

## Quick Start

### 1. Deploy the routes

Add to `backend/server.py`:

```python
from add_all_routes import router as all_router
app.include_router(all_router)
```

### 2. Run all imports

```bash
cd backend
python run_all_imports.py
```

### 3. Test the endpoints

```bash
# Check data source status
curl http://localhost:8000/api/data-sources/status

# Get distressed properties
curl http://localhost:8000/api/distressed-properties?filter_type=violations

# Search TAD by address
curl http://localhost:8000/api/tad/search?query=123+Main+St&search_type=address

# Free skip tracing
curl "http://localhost:8000/api/skip-trace?address=123+Main+St+Fort+Worth+TX"

# Import SmartPropLeads
curl -X POST "http://localhost:8000/api/import/smartpropleads?limit=100"

# Get SmartPropLeads lead types
curl http://localhost:8000/api/smartpropleads/lead-types
```

## Frontend Updates

### New Filter Chips
Add these to your filters in `server.py`:

```python
{"key": "distressed", "label": "Distressed", "count": 0},
{"key": "code_violation", "label": "Code Violation", "count": 0},
{"key": "vacant", "label": "Vacant", "count": 0},
{"key": "wholesale", "label": "Wholesale", "count": 0},
{"key": "absentee", "label": "Absentee Owner", "count": 0},
{"key": "tax-delinquent", "label": "Tax Delinquent", "count": 0},
{"key": "pre-foreclosure", "label": "Pre-Foreclosure", "count": 0},
{"key": "high-equity", "label": "High Equity", "count": 0},
```

### Property Card Updates
Use `PropertyCardWithDistress.tsx` for enhanced cards with:
- Code Violation badge
- Vacant badge
- Distress score pill
- Wholesale badge
- AI Score badge (from SmartPropLeads)

## Legal Notes

All data sources used are:
- ✅ Public government APIs (ArcGIS)
- ✅ Public marketplace pages (scraping)
- ✅ Public records (CSV)
- ✅ No terms of service violations
- ✅ No API keys required

## Next Steps

1. Deploy to Railway
2. Run the imports
3. Add filter chips to frontend
4. Test skip tracing
5. Build automated daily imports (optional)

## Need More Data?

If you want additional data sources that are still free:
- **Harris County (Houston)** - Similar ArcGIS APIs
- **Dallas County** - Public parcel data
- **Census Data** - Demographics, income
- **USPS** - Address validation

All of these are free public APIs.
