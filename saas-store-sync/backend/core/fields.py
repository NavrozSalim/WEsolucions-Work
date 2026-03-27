"""
Encrypted storage for sensitive values (e.g. store API tokens).
Requires: cryptography, ENCRYPTION_KEY in settings (32-byte base64 Fernet key).
"""
import base64
import logging
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


def _get_fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    key = getattr(settings, 'ENCRYPTION_KEY', None) or ''
    if isinstance(key, str):
        key = key.strip()
    if key:
        try:
            key_b = key.encode() if isinstance(key, str) else key
            return Fernet(key_b)
        except Exception as e:
            logger.warning("ENCRYPTION_KEY invalid (%s); using SECRET_KEY-derived key", e)

    # Fallback: derive 32-byte key from SECRET_KEY
    secret = (getattr(settings, 'SECRET_KEY', None) or 'insecure').encode()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b'wesolutions', iterations=480000)
    derived = base64.urlsafe_b64encode(kdf.derive(secret))
    return Fernet(derived)


class EncryptedTextField(models.TextField):
    """
    Store encrypted text in DB. Encrypt on write, decrypt on read.
    Set ENCRYPTION_KEY in env (Fernet.generate_key().decode()).
    """

    def get_db_prep_value(self, value, connection, prepared=False):
        value = super().get_db_prep_value(value, connection, prepared)
        if value:
            try:
                return _get_fernet().encrypt(value.encode()).decode()
            except Exception as e:
                logger.exception("Encryption failed: %s", e)
                raise
        return value

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except Exception as e:
            logger.warning("Decryption failed (value may be plaintext): %s", e)
            return value
