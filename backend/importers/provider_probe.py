"""Opt-in single-record provider probes; no database writes or calls on import."""
import os
import re
import httpx

class ProviderError(RuntimeError):
    pass

def _get(url, params, header=None, env=None):
    headers = {"Accept": "application/json"}
    if env:
        key = os.getenv(env, "").strip()
        if not key:
            raise ProviderError(f"{env} not configured")
        headers[header] = key
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise ProviderError("Invalid provider response")
        return payload
    except (httpx.HTTPError, ValueError):
        raise ProviderError("Provider request failed") from None

def attom_by_address(street, city_state_zip):
    if not str(street).strip() or not str(city_state_zip).strip():
        raise ValueError("Address required")
    return _get("https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile",
                {"address1": street, "address2": city_state_zip}, "apikey", "ATTOM_API_KEY")

def repliers_listing(mls_number):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", mls_number or ""):
        raise ValueError("Invalid MLS identifier")
    return _get(f"https://api.repliers.io/listings/{mls_number}", {},
                "REPLIERS-API-KEY", "REPLIERS_API_KEY")

def tarrant_by_account(account):
    if not re.fullmatch(r"[A-Za-z0-9]{1,30}", account or ""):
        raise ValueError("Invalid TAD account")
    payload = _get("https://mapit.tarrantcounty.com/arcgis/rest/services/Dynamic/TADParcels/FeatureServer/0/query",
                   {"where": f"ACCOUNT='{account}'", "outFields": "ACCOUNT,TAXPIN,OWNER_NAME,SubdivisionName",
                    "returnGeometry": "false", "resultRecordCount": 5, "f": "json"})
    features = payload.get("features", [])
    if not isinstance(features, list):
        raise ProviderError("Invalid ArcGIS features")
    return [f["attributes"] for f in features if isinstance(f, dict) and isinstance(f.get("attributes"), dict)]
