from importers.fort_worth_code_violations import _aggregate, _is_target_complaint


def test_only_requested_complaint_types_are_targeted():
    assert _is_target_complaint("High Grass / Weeds")
    assert _is_target_complaint("Substandard Building")
    assert _is_target_complaint("Property Maintenance")
    assert not _is_target_complaint("Vehicle Parking")
    assert not _is_target_complaint("Zoning Violation")


def test_aggregate_filters_out_open_cases_outside_target_categories():
    features = [
        {"attributes": {
            "Violation_Address": "123 Main St",
            "City": "Fort Worth",
            "Complaint_Type_Description": "High Grass",
            "Violation_Current_Status": "Open",
        }},
        {"attributes": {
            "Violation_Address": "456 Main St",
            "City": "Fort Worth",
            "Complaint_Type_Description": "Vehicle Parking",
            "Violation_Current_Status": "Open",
        }},
    ]

    records = _aggregate(features, target_only=True)

    assert len(records) == 1
    assert records[0]["situs_address"].startswith("123 Main St")
    assert records[0]["code_high_grass_weeds"] is True
