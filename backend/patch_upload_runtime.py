"""Railway startup patch for the public County uploader.

Keeps the deployed uploader deterministic while the legacy bulk importer is being retired.
The upload request only parses, matches, and stores rows. Network enrichment is deferred.
"""
from pathlib import Path
import re

path = Path("bulk_import.py")
text = path.read_text()

# Always use the deterministic matcher.
text = text.replace(
    "from intake import upsert_import_records",
    "from intake_v3 import upsert_import_records",
)
text = text.replace(
    "from intake_v2 import upsert_import_records",
    "from intake_v3 import upsert_import_records",
)

# Owner is a real source type, not a guessed distress category.
needle = '    return ["uploaded"]'
if 'if "owner" in name:' not in text and needle in text:
    text = text.replace(
        needle,
        '    if "owner" in name:\\n        return ["owner"]\\n' + needle,
        1,
    )

# Never make the browser wait on live County/API enrichment after an upload.
text = re.sub(
    r'county\s*=\s*await\s+enrich_live_properties_from_county_records\([^\\n]*\)\s+if\s+unique\s+else\s+\{[^\\n]*\}',
    'county={"live_checked":0,"enriched":0,"tad_lookups":0,"missing":0,"deferred":True}',
    text,
)
text = text.replace("lookup_missing_tad=True", "lookup_missing_tad=False")

# Remove the now-unused live enrichment import when possible.
text = text.replace(
    "    from importers.county_records import enrich_live_properties_from_county_records\\n",
    "",
)

path.write_text(text)
print("County uploader runtime patch applied")
