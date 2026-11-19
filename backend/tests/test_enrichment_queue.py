import asyncio
from types import SimpleNamespace

from enrichment_worker import enqueue_properties


def matches(doc, query):
    for key, value in query.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class Collection:
    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]

    def find(self, query, projection=None):
        rows = [dict(row) for row in self.rows if matches(row, query)]
        class Cursor:
            def limit(self, count): return self
            async def to_list(self, length=None): return rows[:length]
        return Cursor()

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if matches(row, query):
                row.update(update["$set"])
                return


def test_upload_marks_properties_pending_without_touching_processing_rows():
    db = SimpleNamespace(properties=Collection([
        {"id": "a"},
        {"id": "b", "enrichment_status": "processing"},
        {"id": "c", "enrichment_status": "done"},
    ]))
    queued = asyncio.run(enqueue_properties(db, ["a", "b", "c", "missing"]))
    assert queued == 2
    status = {row["id"]: row.get("enrichment_status") for row in db.properties.rows}
    assert status == {"a": "pending", "b": "processing", "c": "pending"}
