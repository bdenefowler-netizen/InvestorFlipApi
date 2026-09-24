"""Startup patch for the public County uploader.

The upload request must only parse, match, store, and return.
Network enrichment is intentionally deferred to background/explicit endpoints.
"""
from pathlib import Path
import re

path = Path("bulk_import.py")
text = path.read_text()

# Always use the deterministic v3 matcher/upserter.
text = text.replace(
    "from intake import upsert_import_records",
    "from intake_v3 import upsert_import_records",
)
text = text.replace(
    "from intake_v2 import upsert_import_records",
    "from intake_v3 import upsert_import_records",
)

# Never wait on live County/TAD/API enrichment after an upload.
text = re.sub(
    r'county\s*=\s*await\s+enrich_live_properties_from_county_records\([D\S]*?\)\s+if\s+unique\s+else\s+\{[D\S]*?\}',
    'county={"live_checked":0,"enriched":0,"tad_lookups":0,"missing":0,"deferred":True}',
    text,
)

# The enricher import is not needed on the request hot path.
text = text.replace(
    "    from importers.county_records import enrich_live_properties_from_county_records\n",
    "",
)

path.write_text(text)
print("County uploader hot-path patch applied")
