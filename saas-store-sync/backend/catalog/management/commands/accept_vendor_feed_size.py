"""Accept a smaller Costway / Vevor feed after the vendor really removed products.

A feed with less than half the rows of the last good one fails (scrapers.feed_format). When that
drop is real, run:  python manage.py accept_vendor_feed_size costway   (or vevor)
The next feed is then taken as the new normal size.
"""
from django.core.management.base import BaseCommand

from scrapers.feed_format import forget_last_good, last_good_rows

KINDS = ('costway', 'vevor')


class Command(BaseCommand):
    help = 'Accept a smaller Costway or Vevor feed (forget the last good row count).'

    def add_arguments(self, parser):
        parser.add_argument('vendor', choices=KINDS)

    def handle(self, *args, **opts):
        kind = opts['vendor']
        before = last_good_rows(kind)
        forget_last_good(kind)
        self.stdout.write(
            f'{kind}: last good row count {before or "not set"} cleared. '
            'The next feed sets the new size.'
        )
