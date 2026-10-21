import asyncio

import httpx

from importers.arcgis_client import (
    ArcGISClient,
    ArcGISQueryError,
    clean_search_text,
    sql_like_contains,
    sql_literal,
)
from importers.fort_worth_permits import permit_is_open, summarize_permits
from importers.tad_scraper import TAD_PROPERTY_LAYER_URL


def test_arcgis_client_paginates_and_detects_embedded_errors():
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("resultOffset", 0))
        offsets.append(offset)
        if request.url.params.get("where") == "bad":
            return httpx.Response(
                200,
                json={"error": {"message": "Invalid query", "details": ["bad field"]}},
            )
        rows = (
            [{"attributes": {"ObjectId": offset + i}} for i in range(2)]
            if offset == 0
            else []
        )
        return httpx.Response(200, json={"features": rows})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ArcGISClient(
                "https://example.test/FeatureServer/0", page_size=2, client=http
            )
            rows = await client.fetch_all(order_by="ObjectId")
            assert len(rows) == 2
            try:
                await client.query(where="bad")
            except ArcGISQueryError as exc:
                assert "Invalid query" in str(exc)
            else:
                raise AssertionError("Expected ArcGISQueryError")

    asyncio.run(run())
    assert offsets[:2] == [0, 2]


def test_search_terms_are_bounded_and_sql_quotes_are_escaped():
    assert (
        clean_search_text("  100 Main St; DROP TABLE x  ") == "100 Main St DROP TABLE x"
    )
    assert sql_literal("O'Brien") == "'O''Brien'"
    assert sql_like_contains("O'Brien%") == "'%O''Brien%'"


def test_tad_uses_the_full_parcel_layer_not_the_county_owned_subset():
    assert TAD_PROPERTY_LAYER_URL.endswith("Dynamic/TADParcels/FeatureServer/0")


def test_permit_summary_counts_open_and_preserves_official_fields():
    summary = summarize_permits(
        [
            {
                "Permit_No": "PB26-1",
                "Permit_Type": "Residential Building Permit",
                "B1_WORK_DESC": "Foundation repair",
                "Full_Street_Address": "100 MAIN ST",
                "Zip_Code": 76102,
                "Current_Status": "Issued",
                "File_Date": 1792454400000,
                "JobValue": "$12,500",
            },
            {
                "Permit_No": "PB25-2",
                "Full_Street_Address": "100 MAIN ST",
                "Current_Status": "Finaled",
                "File_Date": 1760918400000,
            },
        ]
    )
    assert summary["permit_count"] == 2
    assert summary["open_permit_count"] == 1
    assert summary["latest_permit"]["permit_number"] == "PB26-1"
    assert summary["latest_permit"]["job_value"] == 12500
    assert permit_is_open("Issued") is True
    assert permit_is_open("Finaled") is False
