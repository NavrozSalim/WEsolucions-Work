from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from core import fields


class EncryptionFieldTests(SimpleTestCase):
    @override_settings(DEBUG=False, ENCRYPTION_KEY="")
    def test_missing_encryption_key_raises_in_non_debug(self):
        with self.assertRaises(RuntimeError):
            fields._get_fernet()

    @override_settings(DEBUG=True, ENCRYPTION_KEY="")
    def test_debug_mode_uses_ephemeral_key(self):
        f1 = fields._get_fernet()
        f2 = fields._get_fernet()
        self.assertIsNotNone(f1)
        self.assertEqual(f1, f2)

    @override_settings(DEBUG=False, ENCRYPTION_KEY="invalid-key")
    def test_invalid_encryption_key_raises(self):
        with self.assertRaises(RuntimeError):
            fields._get_fernet()

    @override_settings(DEBUG=False, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_encrypted_text_field_roundtrip(self):
        field = fields.EncryptedTextField()
        plain = "super-secret-value"
        encrypted = field.get_prep_value(plain)
        self.assertNotEqual(encrypted, plain)
        self.assertEqual(field.from_db_value(encrypted, None, None), plain)
