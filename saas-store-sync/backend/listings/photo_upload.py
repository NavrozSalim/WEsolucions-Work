"""Save listing photos to MEDIA and return public URLs for marketplace publish."""
from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_FILES = 12


class PhotoUploadError(ValueError):
    pass


def _ext_for(upload: UploadedFile) -> str:
    name = (upload.name or "").lower()
    suffix = Path(name).suffix
    if suffix in ALLOWED_EXTENSIONS:
        return suffix
    ctype = (upload.content_type or "").lower().strip()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if ctype in mapping:
        return mapping[ctype]
    raise PhotoUploadError(f'Unsupported image type for "{upload.name or "file"}". Use JPG, PNG, WEBP, or GIF.')


def absolute_media_url(request, relative_url: str) -> str:
    """Build a URL Reverb (or browsers) can fetch. Prefer PUBLIC_MEDIA_BASE_URL."""
    rel = relative_url if relative_url.startswith("/") else f"/{relative_url}"
    base = (getattr(settings, "PUBLIC_MEDIA_BASE_URL", None) or "").strip().rstrip("/")
    if base:
        return f"{base}{rel}"
    try:
        return request.build_absolute_uri(rel)
    except Exception:  # noqa: BLE001
        # e.g. DisallowedHost in tests — fall back to relative path as absolute-ish URL
        frontend = (getattr(settings, "FRONTEND_URL", None) or "").strip().rstrip("/")
        if frontend:
            # Prefer API host if FRONTEND is only the SPA; still better than a bare path.
            return f"{frontend}{rel}"
        return rel


def save_listing_photos(request, store, uploads: list) -> list[dict]:
    """Persist uploaded images under media/listing_photos/{store_id}/.

    Returns list of {url, path, name} dicts.
    """
    if not uploads:
        raise PhotoUploadError("Choose at least one image file.")
    if len(uploads) > MAX_FILES:
        raise PhotoUploadError(f"You can upload at most {MAX_FILES} images at a time.")

    results = []
    store_id = str(getattr(store, "id", "store"))
    for upload in uploads:
        if not isinstance(upload, UploadedFile):
            continue
        if upload.size and upload.size > MAX_BYTES:
            raise PhotoUploadError(
                f'"{upload.name}" is too large (max {MAX_BYTES // (1024 * 1024)} MB).'
            )
        ctype = (upload.content_type or "").lower().strip()
        if ctype and ctype not in ALLOWED_CONTENT_TYPES and not ctype.startswith("image/"):
            raise PhotoUploadError(f'"{upload.name}" is not a supported image.')
        ext = _ext_for(upload)
        filename = f"{uuid.uuid4().hex}{ext}"
        relative_path = f"listing_photos/{store_id}/{filename}"
        saved = default_storage.save(relative_path, upload)
        media_url = settings.MEDIA_URL.rstrip("/") + "/" + saved.replace("\\", "/")
        results.append(
            {
                "url": absolute_media_url(request, media_url),
                "path": saved,
                "name": upload.name or filename,
            }
        )
    if not results:
        raise PhotoUploadError("No valid image files were uploaded.")
    return results
