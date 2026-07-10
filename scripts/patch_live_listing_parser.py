from __future__ import annotations

from pathlib import Path

SERVER = Path("backend/server.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        if new in text:
            print(f"{label}: already patched")
            return text
        raise RuntimeError(f"{label}: expected source block not found")
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    print(f"{label}: patched")
    return text.replace(old, new, 1)


def main() -> None:
    text = SERVER.read_text(encoding="utf-8")
    original = text

    text = text.replace('"sort": "Newest"', '"sort": "NEWEST"')

    old_address = '''def _extract_address(item: Dict[str, Any]) -> Dict[str, str]:
    address_obj = item.get("address") if isinstance(item.get("address"), dict) else {}
    location_obj = item.get("location") if isinstance(item.get("location"), dict) else {}

    street = (
        item.get("streetAddress") or item.get("street_address") or item.get("street")
        or address_obj.get("streetAddress") or address_obj.get("street_address") or address_obj.get("street")
        or location_obj.get("streetAddress") or location_obj.get("street")
        or ""
    )
    city = (
        item.get("city") or address_obj.get("city") or location_obj.get("city") or "Fort Worth"
    )
    state = (
        item.get("state") or address_obj.get("state") or location_obj.get("state") or "TX"
    )
    zipc = (
        item.get("zipcode") or item.get("zip") or item.get("postal_code")
        or address_obj.get("zipcode") or address_obj.get("zip") or address_obj.get("postal_code")
        or ""
    )

    full = (
        item.get("full_address") or item.get("formattedAddress") or item.get("address_line")
        or (f"{street}, {city}, {state} {zipc}".strip(", ") if street else "")
    )

    return {
        "street": str(street).strip(),
        "city": str(city).strip(),
        "state": str(state).strip() or "TX",
        "zip": str(zipc).strip(),
        "full": str(full).strip(),
    }
'''

    new_address = '''def _extract_address(item: Dict[str, Any]) -> Dict[str, str]:
    raw_address = item.get("address")
    address_obj = raw_address if isinstance(raw_address, dict) else {}
    location_obj = item.get("location") if isinstance(item.get("location"), dict) else {}
    location_address = location_obj.get("address") if isinstance(location_obj.get("address"), dict) else {}

    street = (
        item.get("streetAddress") or item.get("street_address") or item.get("street")
        or item.get("address1") or item.get("addressLine") or item.get("address_line_1")
        or address_obj.get("streetAddress") or address_obj.get("street_address")
        or address_obj.get("street") or address_obj.get("line") or address_obj.get("address1")
        or location_obj.get("streetAddress") or location_obj.get("street")
        or location_address.get("streetAddress") or location_address.get("street")
        or location_address.get("line") or ""
    )
    city = (
        item.get("city") or item.get("addressCity") or item.get("locality")
        or address_obj.get("city") or address_obj.get("locality")
        or location_obj.get("city") or location_obj.get("locality")
        or location_address.get("city") or "Fort Worth"
    )
    state = (
        item.get("state") or item.get("addressState") or item.get("region")
        or address_obj.get("state") or address_obj.get("region")
        or location_obj.get("state") or location_obj.get("region")
        or location_address.get("state") or "TX"
    )
    zipc = (
        item.get("zipcode") or item.get("zip") or item.get("postal_code") or item.get("postalCode")
        or address_obj.get("zipcode") or address_obj.get("zip")
        or address_obj.get("postal_code") or address_obj.get("postalCode")
        or location_obj.get("postal_code") or location_obj.get("postalCode")
        or location_address.get("postal_code") or location_address.get("postalCode") or ""
    )

    full = (
        (raw_address if isinstance(raw_address, str) else "")
        or item.get("full_address") or item.get("fullAddress")
        or item.get("formattedAddress") or item.get("formatted_address")
        or item.get("address_line") or item.get("addressLine")
        or address_obj.get("formattedAddress") or address_obj.get("formatted_address")
        or location_obj.get("formattedAddress") or location_obj.get("formatted_address")
        or (f"{street}, {city}, {state} {zipc}".strip(", ") if street else "")
    )

    return {
        "street": str(street).strip(),
        "city": str(city).strip(),
        "state": str(state).strip() or "TX",
        "zip": str(zipc).strip(),
        "full": str(full).strip(),
    }
'''
    text = replace_once(text, old_address, new_address, "address parser")

    old_type = '''    raw_type = (
        item.get("homeType") or item.get("home_type") or item.get("propertyType") or
        item.get("property_type") or item.get("propertySubType") or item.get("type") or
        "Single Family Residential"
    )
'''
    new_type = '''    raw_type = (
        item.get("homeType") or item.get("home_type") or item.get("propertyType")
        or item.get("property_type") or item.get("propertySubType")
        or item.get("property_sub_type") or item.get("propertyTypeText")
        or item.get("property_type_name") or item.get("style") or item.get("type")
    )
    # The us-real-estate-listings request is explicitly constrained to
    # property_type=single_family, so a missing type field is safe to infer here.
    if not raw_type and "us-real-estate-listings" in source_name.lower():
        raw_type = "Single Family Residential"
'''
    text = replace_once(text, old_type, new_type, "property type parser")

    text = text.replace(
        'item.get("price") or item.get("listPrice") or item.get("list_price") or item.get("unformattedPrice")',
        'item.get("price") or item.get("listPrice") or item.get("list_price") or item.get("asking_price") or item.get("unformattedPrice")',
    )
    text = text.replace(
        'item.get("beds") or item.get("bedrooms")',
        'item.get("beds") or item.get("bedrooms") or item.get("bedroom_count")',
    )
    text = text.replace(
        'item.get("baths") or item.get("bathrooms") or item.get("bathroomsFloat")',
        'item.get("baths") or item.get("bathrooms") or item.get("bathroom_count") or item.get("bathroomsFloat")',
    )
    text = text.replace(
        'item.get("livingArea") or item.get("living_area") or item.get("area") or item.get("area_sqft")',
        'item.get("livingArea") or item.get("living_area") or item.get("square_feet") or item.get("building_size") or item.get("area") or item.get("area_sqft")',
    )
    text = text.replace(
        'item.get("zpid") or item.get("property_id") or item.get("listing_id") or item.get("id")',
        'item.get("zpid") or item.get("property_id") or item.get("propertyId") or item.get("listing_id") or item.get("listingId") or item.get("id")',
    )
    text = text.replace(
        'for k in ["imgSrc", "image", "image_url", "hiResImageLink"]:',
        'for k in ["imgSrc", "image", "image_url", "photo_url", "primary_photo", "hiResImageLink"]:',
    )
    text = text.replace(
        'item.get("detailUrl") or item.get("url")',
        'item.get("detailUrl") or item.get("detail_url") or item.get("href") or item.get("url")',
    )

    if text == original:
        print("No changes needed.")
        return

    SERVER.write_text(text, encoding="utf-8")
    print("Live listing parser patch complete.")


if __name__ == "__main__":
    main()
