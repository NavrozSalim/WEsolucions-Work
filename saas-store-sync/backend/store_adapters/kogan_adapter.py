import json
import logging
from dataclasses import dataclass

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from .base import BaseStoreAdapter

logger = logging.getLogger(__name__)


def _col_index_to_letter(col_idx_0: int) -> str:
    """0-based column index -> Google Sheets column letters (A, B, ..., Z, AA, AB...)."""
    if col_idx_0 is None or col_idx_0 < 0:
        return "A"
    n = col_idx_0 + 1
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _clean_sku(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _loads_service_account_json(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError("Invalid JSON: could not parse service account key") from e
    if not isinstance(obj, dict):
        raise ValueError("Invalid JSON: expected an object")
    if obj.get("type") != "service_account":
        raise ValueError("Invalid JSON: type must be service_account")
    if not obj.get("client_email") or not obj.get("private_key"):
        raise ValueError("Invalid JSON: missing client_email or private_key")
    return obj


@dataclass
class KoganSheetConfig:
    spreadsheet_id: str
    tab_name: str
    sku_col_name: str = "PRODUCT_SKU"
    stock_col_name: str = "STOCK"
    price_col_name: str = "kogan_first_price"


class KoganAdapter(BaseStoreAdapter):
    """
    Kogan integration via Google Sheets.

    Instead of an HTTP marketplace API, we update the user's Kogan inventory/price
    sheet by SKU.
    """

    def __init__(self, store):
        super().__init__(store)
        self._service = None
        self._header_cache = None

    def _get_service_account_raw(self) -> str:
        raw = (getattr(self.store, "kogan_service_account_json", None) or "").strip()
        if raw:
            return raw
        # Back-compat: some setups stored JSON in api_token
        tok = (getattr(self.store, "api_token", None) or "").strip()
        if tok and tok.lstrip().startswith("{"):
            return tok
        return ""

    def _config(self) -> KoganSheetConfig:
        sheet_id = (getattr(self.store, "kogan_sheet_id", None) or "").strip()
        tab = (getattr(self.store, "kogan_tab_name", None) or "").strip()
        if not sheet_id:
            raise ValueError("Missing Kogan Spreadsheet ID (kogan_sheet_id).")
        if not tab:
            raise ValueError("Missing Kogan Tab name (kogan_tab_name).")
        return KoganSheetConfig(
            spreadsheet_id=sheet_id,
            tab_name=tab,
            sku_col_name=(getattr(self.store, "kogan_sku_column", None) or "PRODUCT_SKU").strip() or "PRODUCT_SKU",
            stock_col_name=(getattr(self.store, "kogan_stock_column", None) or "STOCK").strip() or "STOCK",
            price_col_name=(
                getattr(self.store, "kogan_first_price_column", None) or "kogan_first_price"
            ).strip()
            or "kogan_first_price",
        )

    def _get_service(self):
        if self._service is not None:
            return self._service
        raw = self._get_service_account_raw()
        if not raw:
            raise ValueError("Missing Kogan service account JSON key.")
        info = _loads_service_account_json(raw)
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        # cache_discovery=False avoids writing discovery cache in some environments
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def _get_headers(self) -> list[str]:
        if self._header_cache is not None:
            return self._header_cache
        cfg = self._config()
        service = self._get_service()
        res = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=cfg.spreadsheet_id, range=f"{cfg.tab_name}!1:1")
            .execute()
        )
        headers = (res.get("values") or [[]])[0]
        self._header_cache = [str(h).strip() for h in headers]
        return self._header_cache

    def _validate_sheet_columns(self):
        cfg = self._config()
        headers = self._get_headers()
        missing = []
        for col in (cfg.sku_col_name, cfg.stock_col_name, cfg.price_col_name):
            if col not in headers:
                missing.append(col)
        if missing:
            raise ValueError(
                f"Google Sheet missing required column(s): {', '.join(missing)}. "
                f"Found headers: {headers[:30]}"
            )
        return True

    def validate_connection(self):
        # "Connection" = can authenticate + read headers + required columns exist.
        self._validate_sheet_columns()
        return True

    # --- Adapter API ---
    def lookup_listing_by_sku(self, sku: str):
        # For Sheets, the SKU *is* the identifier.
        return _clean_sku(sku)

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError("Kogan sheets integration does not create products; it updates existing rows by SKU.")

    def update_inventory(self, external_id, stock):
        return self.update_product(external_id, stock=stock)

    def delete_product(self, external_id):
        raise NotImplementedError("Kogan sheets integration does not delete products; use Kogan tools to manage listings.")

    def update_product(self, external_id, **kwargs):
        """
        Non-bulk fallback: update a single SKU (used if sync task doesn't call bulk).
        """
        sku = _clean_sku(external_id)
        if not sku:
            raise ValueError("Missing SKU for Kogan update.")
        price = kwargs.get("price")
        stock = kwargs.get("stock")
        self.update_products_bulk([(sku, price, stock)])
        return True

    def update_products_bulk(self, items: list[tuple[str, float | None, int | None]]):
        """
        Bulk update many SKU rows in a single run.

        items: list of (sku, price, stock)
        Returns: {'ok': set(sku), 'failed': [{'sku':..., 'error':...}, ...]}
        """
        if not items:
            return {"ok": set(), "failed": []}

        cfg = self._config()
        self._validate_sheet_columns()
        headers = self._get_headers()
        service = self._get_service()

        sku_idx = headers.index(cfg.sku_col_name)
        stock_idx = headers.index(cfg.stock_col_name)
        price_idx = headers.index(cfg.price_col_name)

        # Read all SKU values in the sheet once to build SKU -> row mapping
        sku_col_letter = _col_index_to_letter(sku_idx)
        res = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=cfg.spreadsheet_id,
                range=f"{cfg.tab_name}!{sku_col_letter}2:{sku_col_letter}",
            )
            .execute()
        )
        sku_rows = res.get("values") or []
        sku_to_row = {}
        for i, row in enumerate(sku_rows):
            row_num = i + 2
            cell = row[0] if row else ""
            s = _clean_sku(cell)
            if s and s not in sku_to_row:
                sku_to_row[s] = row_num

        updates = []
        ok = set()
        failed = []

        def _add_cell_update(col_idx: int, row_num: int, value):
            col_letter = _col_index_to_letter(col_idx)
            updates.append(
                {
                    "range": f"{cfg.tab_name}!{col_letter}{row_num}",
                    "values": [[value]],
                }
            )

        for sku, price, stock in items:
            s = _clean_sku(sku)
            if not s:
                continue
            row_num = sku_to_row.get(s)
            if not row_num:
                failed.append({"sku": s, "error": "SKU not found in Google Sheet"})
                continue
            # Only update provided fields
            if stock is not None:
                try:
                    _add_cell_update(stock_idx, row_num, int(stock))
                except Exception:
                    _add_cell_update(stock_idx, row_num, stock)
            if price is not None:
                _add_cell_update(price_idx, row_num, float(price))
            ok.add(s)

        if not updates:
            return {"ok": ok, "failed": failed}

        # Google Sheets API limit: 1000 updates per batchUpdate request.
        batch_size = 1000
        for i in range(0, len(updates), batch_size):
            batch = updates[i : i + batch_size]
            (
                service.spreadsheets()
                .values()
                .batchUpdate(
                    spreadsheetId=cfg.spreadsheet_id,
                    body={"valueInputOption": "RAW", "data": batch},
                )
                .execute()
            )

        return {"ok": ok, "failed": failed}

