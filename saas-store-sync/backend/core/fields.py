"""
Encrypted storage for sensitive values (e.g. store API tokens).
Production requires ENCRYPTION_KEY; debug can auto-generate an in-memory key.
"""
import logging
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

_DEV_FERNET = None


def _get_fernet():
    from cryptography.fernet import Fernet

    global _DEV_FERNET
    key = getattr(settings, 'ENCRYPTION_KEY', None) or ''
    if isinstance(key, str):
        key = key.strip()
    if key:
        key_b = key.encode() if isinstance(key, str) else key
        try:
            return Fernet(key_b)
        except Exception as exc:
            raise RuntimeError("Invalid ENCRYPTION_KEY: expected valid Fernet key") from exc

    if getattr(settings, 'DEBUG', False):
        if _DEV_FERNET is None:
            _DEV_FERNET = Fernet(Fernet.generate_key())
            logger.warning("ENCRYPTION_KEY is missing in DEBUG mode; using ephemeral in-memory key")
        return _DEV_FERNET

    raise RuntimeError("ENCRYPTION_KEY is required when DEBUG=False")


class EncryptedTextField(models.TextField):
    """
    Store encrypted text in DB. Encrypt on write, decrypt on read.
    Set ENCRYPTION_KEY in env (Fernet.generate_key().decode()).
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return _get_fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        return _get_fernet().decrypt(value.encode()).decode()

    def to_python(self, value):
        if value in (None, ''):
            return value
        if isinstance(value, str) and value.startswith('gAAAA'):
            try:
                return _get_fernet().decrypt(value.encode()).decode()
            except Exception:
                return value
        return value

    def get_db_prep_value(self, value, connection, prepared=False):
        value = super().get_db_prep_value(value, connection, prepared)
        return value
