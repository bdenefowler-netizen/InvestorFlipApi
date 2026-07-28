# Fort Worth Distressed Properties Integration

## Overview

This integration adds **real distressed property data** from Fort Worth, TX to InvestorFlip. It pulls:

1. **Code Violations** — Properties with active violations (vacant structures, junk vehicles, overgrown vegetation)
2. **Foreclosure Records** — Tarrant County tax lien sales and pre-foreclosure properties
3. **Distress Scoring** — Automated scoring based on violation count, type, and severity

## Data Sources

| Source | API | Records | Update Frequency |
|--------|-----|---------|------------------|
| Fort Worth Code Violations | ArcGIS Feature Service | 600+ | Real-time |
| Tarrant County Foreclosures | Public Records CSV | 20+ | Monthly |
| Tarrant County Tax Roll | Master.dat + Rec.DAT | 2.75M | Daily |

## Integration Steps

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Import Data

```bash
# Import Fort Worth Code Violations
python -c "
import asyncio
from database import PostgresDatabase
from importers.fort_worth_violations import import_fort_worth_violations

async def main():
    db = PostgresDatabase()
    await db.connect()
    result = await import_fort_worth_violations(db, limit=2000)
    print(result)
    await db.close()

asyncio.run(main())
"

# Import Foreclosures
python -c "
import asyncio
from database import PostgresDatabase
from importers.foreclosure_finder import import_foreclosures

async def main():
    db = PostgresDatabase()
    await db.connect()
    result = await import_foreclosures(db)
    print(result)
    await db.close()

asyncio.run(main())
"
```

### 3. Add Routes to Server

Add these lines to `backend/server.py`:

```python
# Near the imports (around line 40)
from add_violation_routes import router as violation_router

# Near the bottom, before app.include_router(api_router) (around line 2060)
app.include_router(violation_router)
```

### 4. Deploy to Railway

```bash
git add .
git commit -m "feat: Add Fort Worth distressed properties integration"
git push origin feature/investorflip-v1
```

## New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/import/fort-worth-violations` | POST | Import code violations |
| `/api/import/foreclosures` | POST | Import foreclosure records |
| `/api/fort-worth-violations/status` | GET | Check API availability |
| `/api/distressed-properties` | GET | Get distressed properties |

## New Filter Chips (Frontend)

Add these to the filters in `server.py`:

```python
# In the filters endpoint, add:
{"key": "distressed", "label": "Distressed", "count": 0},
{"key": "code_violation", "label": "Code Violation", "count": 0},
{"key": "vacant", "label": "Vacant", "count": 0},
{"key": "nuisance", "label": "Nuisance", "count": 0},
```

## Property Schema Additions

New fields added to property documents:

```typescript
// Code Violation Fields
violation_count: number;        // Total violations
open_violation_count: number;   // Active violations
closed_violation_count: number; // Resolved violations
violation_types: string[];      // Categories: vacant_structure, junk_vehicles, etc.
open_violation_types: string[]; // Active violation categories
case_id: string;                // Fort Worth case ID
code_officer: string;           // Assigned officer
earliest_case_date: string;     // First violation date
latest_violation_date: string;  // Most recent violation
distress_score: number;         // 1-100 distress rating

// Foreclosure Fields
sale_date: string;              // Auction date
opening_bid: number;            // Starting bid amount
trustee: string;                // Trustee name
parcel_id: string;              // County parcel ID
```

## Distress Scoring Algorithm

The distress score (1-100) is calculated based on:

| Factor | Points |
|--------|--------|
| 10+ violations | +40 |
| 5-9 violations | +30 |
| 3-4 violations | +20 |
| 1-2 violations | +10 |
| 5+ open violations | +30 |
| 3-4 open violations | +20 |
| 1-2 open violations | +10 |
| Vacant structure | +20 |
| Nuisance/boarding house | +20 |
| Junk vehicles/pool | +10 |

## Frontend Updates Needed

### PropertyCard.tsx
Add violation badges:

```tsx
{p.violation_count > 0 ? (
  <View style={[styles.miniBadge, { backgroundColor: "#F1D9D5" }]}>
    <Text style={[styles.miniBadgeText, { color: "#7A2A24" }]}>
      CODE VIOLATION
    </Text>
  </View>
) : null}

{p.vacant ? (
  <View style={[styles.miniBadge, { backgroundColor: "#F2E0BD" }]}>
    <Text style={[styles.miniBadgeText, { color: "#5A3F0E" }]}>VACANT</Text>
  </View>
) : null}
```

### Property Detail Page
Add violation details section:

```tsx
{prop.violation_count > 0 ? (
  <View style={styles.section}>
    <Text style={styles.sectionTitle}>CODE VIOLATIONS</Text>
    <View style={styles.card}>
      <KeyValue k="Total Violations" v={String(prop.violation_count)} />
      <KeyValue k="Open" v={String(prop.open_violation_count)} />
      <KeyValue k="Types" v={prop.violation_types?.join(", ") || "N/A"} mono={false} />
      <KeyValue k="Case ID" v={prop.case_id || "N/A"} />
      <KeyValue k="Distress Score" v={`${prop.distress_score}/100`} />
    </View>
  </View>
) : null}
```

## Testing

```bash
# Test the API
curl http://localhost:8000/api/fort-worth-violations/status

# Import violations
curl -X POST http://localhost:8000/api/import/fort-worth-violations?limit=100

# Get distressed properties
curl http://localhost:8000/api/distressed-properties?filter_type=violations&limit=50
```

## Next Steps

1. **Skip Tracing** — Add contact info enrichment for property owners
2. **Map View** — Display distressed properties on a map
3. **Direct Mail** — Generate mail merge files for outreach
4. **Automated Updates** — Cron job to pull new violations daily
5. **Owner Lookup** — Cross-reference with TAD for owner contact info
