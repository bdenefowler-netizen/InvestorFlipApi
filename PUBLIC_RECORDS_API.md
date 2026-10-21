# Public Records API

Read-only endpoints under `/api/public-records` combine official Tarrant County
and City of Fort Worth sources. They do not require a paid RapidAPI provider.

## Endpoints

| Endpoint | Source | Purpose |
| --- | --- | --- |
| `GET /api/public-records/sources` | All configured sources | Source and coverage inventory |
| `GET /api/public-records/tarrant/property?address=...` | TAD parcel layer + local tax-roll snapshot | Property, owner, value, deed, and parcel facts |
| `GET /api/public-records/tarrant/owner?name=...` | TAD parcel layer | Find parcels by owner name |
| `GET /api/public-records/fort-worth/code-violations?address=...` | Fort Worth Open Data | Current and historical code violations |
| `GET /api/public-records/fort-worth/permits?address=...` | Fort Worth Open Data | Development permit history and open-permit count |
| `GET /api/public-records/fort-worth/property-report?address=...` | Combined sources | One property/violations/permits response |
| `GET /api/public-records/catalog/search?q=...` | Socrata Open Data Network | Discover additional public datasets |

Example:

```bash
curl --get 'https://investorflipapi-production-4970.up.railway.app/api/public-records/fort-worth/property-report' \
  --data-urlencode 'address=100 Main St, Fort Worth, TX'
```

## Tarrant County source distinction

`Dynamic/TADParcels/FeatureServer/0` is the full live parcel layer (more than
758,000 records when verified in October 2026). It includes owner, mailing,
situs, deed, building, land, and value fields.

`Tax/TCProperty/MapServer/0` has a similar schema but is a small county-owned
property subset. It must not be treated as the complete appraisal roll.

The existing bulk tax-roll sync remains useful as a durable, searchable local
snapshot. It validates and streams the official dated ZIP instead of loading an
HTML downloads page into pandas or replacing an entire staging table.

## Safety and behavior

- Search input is normalized and SQL literals are escaped before ArcGIS queries.
- Result limits are bounded.
- ArcGIS error objects are treated as failures even when the HTTP status is 200.
- Pagination is handled centrally.
- The combined report returns partial results and per-source errors if one
  public service is temporarily unavailable.
