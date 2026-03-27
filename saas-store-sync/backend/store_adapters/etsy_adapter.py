from .base import BaseStoreAdapter


class EtsyAdapter(BaseStoreAdapter):
    """Etsy API adapter. Implement with Etsy Open API and self._token."""

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError("Etsy create_product: implement with Etsy API")

    def update_product(self, external_id, **kwargs):
        raise NotImplementedError("Etsy update_product: implement with Etsy API")

    def update_inventory(self, external_id, stock):
        raise NotImplementedError("Etsy update_inventory: implement with Etsy API")

    def delete_product(self, external_id):
        raise NotImplementedError("Etsy delete_product: implement with Etsy API")
