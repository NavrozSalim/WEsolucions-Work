from abc import ABC, abstractmethod


class BaseStoreAdapter(ABC):
    """
    Base for platform-specific store APIs. Each adapter receives the Store instance;
    use store.get_api_token() for decrypted token when available.
    """

    def __init__(self, store):
        self.store = store
        # Token: use decrypted value. If Store uses EncryptedTextField, it's auto-decrypted on read.
        self._token = getattr(store, 'api_token', None) or getattr(store, 'get_api_token', lambda: None)()

    @abstractmethod
    def create_product(self, sku, title, price, stock, **kwargs):
        """Create a product on the platform. Return external_id or raise."""
        pass

    @abstractmethod
    def update_product(self, external_id, **kwargs):
        """Update product (e.g. title, price, stock)."""
        pass

    @abstractmethod
    def update_inventory(self, external_id, stock):
        """Update only stock for the given external_id."""
        pass

    @abstractmethod
    def delete_product(self, external_id):
        """Delete or deactivate the product on the platform."""
        pass

    def validate_connection(self):
        """
        Validate API token / connection to the platform.
        Override in adapters to perform real API validation.
        Default: basic token presence/length check.
        """
        return bool(self._token and len(str(self._token)) > 10)
