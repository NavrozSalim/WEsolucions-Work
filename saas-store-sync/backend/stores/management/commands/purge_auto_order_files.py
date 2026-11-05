from django.core.management.base import BaseCommand

from stores.auto_order_bot import purge_expired
from stores.models import Store


class Command(BaseCommand):
    help = "Delete Auto Order Bot order exports from previous days (schedule it daily, e.g. just after midnight)."

    def handle(self, *args, **opts):
        n = sum(1 for s in Store.objects.filter(auto_order_bot_enabled=True) if purge_expired(s))
        self.stdout.write(f'Removed expired order files for {n} store(s).')
