from django.db import models
from stores.models import Store


class DailyStoreMetrics(models.Model):
    """Daily aggregated metrics per store. Retention: 1 year."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='daily_metrics')
    date = models.DateField(db_index=True)
    orders_count = models.IntegerField(default=0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    out_of_stock_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ('store', 'date')
        ordering = ['-date']
        verbose_name_plural = 'Daily store metrics'

    def __str__(self):
        return f"{self.store.name} @ {self.date}"
