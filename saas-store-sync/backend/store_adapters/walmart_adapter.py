from .base import BaseStoreAdapter


class WalmartAdapter(BaseStoreAdapter):
    """Walmart Marketplace API adapter. Implement with Walmart API and self._token, self.store.region."""

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError("Walmart create_product: implement with Walmart API")

    def update_product(self, external_id, **kwargs):
        raise NotImplementedError("Walmart update_product: implement with Walmart API")

    def update_inventory(self, external_id, stock):
        raise NotImplementedError("Walmart update_inventory: implement with Walmart API")

    def delete_product(self, external_id):
        raise NotImplementedError("Walmart delete_product: implement with Walmart API")
