"""LasooConnect query specs: URL paths, query names, and payload builders.

Lasoo URLs follow: {base_url}/{Module}/{Action}/1.0.0
Payloads require matching ``query`` and ``name`` fields or Lasoo returns
"Query does not exist. Check the name and version".
"""

QUERY_SPECS = {
    "test": {
        "path": "/Core/TestNoAuthentication/1.0.0",
        "query": "Core_TestNoAuthentication",
        "name": "Core - TestNoAuthentication : 1.0.0",
        "requires_auth": False,
    },
    "bulk_upsert": {
        "path": "/Variants/BulkUpsert/1.0.0",
        "query": "Variants_BulkUpsert",
        "name": "Variants - BulkUpsert : 1.0.0",
        "requires_auth": True,
    },
    "bulk_delete": {
        "path": "/Variants/BulkDelete/1.0.0",
        "query": "Variants_BulkDelete",
        "name": "Variants - BulkDelete : 1.0.0",
        "requires_auth": True,
    },
    "variants_search": {
        "path": "/Variants/Search/1.0.0",
        "query": "Variants_Search",
        "name": "Variants - Search : 1.0.0",
        "requires_auth": True,
    },
    "orders": {
        "path": "/Invoices/Search/1.0.0",
        "query": "Invoices_Search",
        "name": "Invoices - Search : 1.0.0",
        "requires_auth": True,
    },
    "create_test_order": {
        "path": "/Orders/CreateTestOrder/1.0.0",
        "query": "Orders_CreateTestOrder",
        "name": "Orders - CreateTestOrder : 1.0.0",
        "requires_auth": True,
    },
    "shipping": {
        "path": "/Shipments/Upsert/1.0.0",
        "query": "Shipments_Upsert",
        "name": "Shipments - Upsert : 1.0.0",
        "requires_auth": True,
    },
    "shipments_search": {
        "path": "/Shipments/Search/1.0.0",
        "query": "Shipments_Search",
        "name": "Shipments - Search : 1.0.0",
        "requires_auth": True,
    },
}

DEFAULT_ENDPOINTS = {key: spec["path"] for key, spec in QUERY_SPECS.items()}

DEFAULT_STAGING_BASE_URL = "https://stage.api.lasoo.com.au"
DEFAULT_PRODUCTION_BASE_URL = "https://api.lasoo.com.au"


def build_payload(endpoint_key: str, data=None, auth: str | None = None) -> dict:
    """Build a LasooConnect JSON body for the given endpoint key."""
    spec = QUERY_SPECS[endpoint_key]
    payload = {
        "name": spec["name"],
        "version": "1.0.0",
        "query": spec["query"],
        "data": data if data is not None else {},
        "results": {"name": "results"},
    }
    if spec.get("requires_auth") and auth:
        payload["auth"] = auth
    return payload


def extract_lasoo_failure_message(body) -> str | None:
    """Return a user-facing failure message if Lasoo indicates failure in the body."""
    if body is None:
        return None
    if isinstance(body, str):
        text = body.strip()
        if text.lower().startswith("fail"):
            return text
        return None
    if not isinstance(body, dict):
        return None

    for key in ("message", "error", "detail", "title", "description"):
        val = body.get(key)
        if isinstance(val, str) and val.strip().lower().startswith("fail"):
            return val.strip()

    results = body.get("results")
    if isinstance(results, dict):
        for key in ("message", "error", "detail", "name", "description"):
            val = results.get(key)
            if isinstance(val, str) and val.strip().lower().startswith("fail"):
                return val.strip()

    return None
