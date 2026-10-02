"""Nora and Wallkoala spreadsheets stored in Postgres.

The AU worker does not share the main server's media disk. The bytes saved
on upload are what that worker reads.
"""
from __future__ import annotations

import io


class NamedBytes(io.BytesIO):
    """File-like wrapper so the Excel parsers accept a database copy."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name or 'inventory.xlsx'

    def chunks(self, chunk_size: int = 64 * 1024):
        self.seek(0)
        while True:
            chunk = self.read(chunk_size)
            if not chunk:
                break
            yield chunk


def read_saved_inventory(inv) -> bytes:
    """Return the spreadsheet bytes, from the database first.

    When only the local file exists, copy it into the database so the next
    read on another server does not need that disk.
    """
    blob = getattr(inv, 'inventory_file_bytes', None)
    if blob:
        return bytes(blob)
    field = getattr(inv, 'nora_inventory_file', None)
    if not field:
        return b''
    try:
        field.open('rb')
        data = field.read() or b''
    except Exception:
        return b''
    finally:
        try:
            field.close()
        except Exception:
            pass
    if data:
        inv.inventory_file_bytes = data
        inv.save(update_fields=['inventory_file_bytes'])
    return data
