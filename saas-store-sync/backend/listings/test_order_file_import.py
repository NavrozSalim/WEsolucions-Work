"""Auto Purchasing Phase 2: import Kogan / BigW order files into MarketplaceOrder."""
import io

import openpyxl
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from listings.models import MarketplaceOrder, OrderStatus
from listings.order_file_import import OrderFileError, parse_order_file
from marketplace.models import Marketplace
from stores.models import Store

User = get_user_model()

KOGAN_HEADERS = [
    'Dispatch ETD', 'DeliveryName', 'DeliveryAddress1', 'DeliveryAddress2', 'DeliverySuburb',
    'DeliveryState', 'DeliveryPostCode', 'Quantity', 'Product Description', 'ProductCode', 'OrderID',
    'DeliveryEmailAddress', 'DeliveryPhone', 'OrderRow', 'LabelInfo', 'OriginWarehouse', 'DestCountry',
    'OrderDate', 'WebReference', 'ItemPrice',
]
KOGAN_ROWS = [
    ['2026-11-04T00:00:00', 'Sheryl Raymer', '3 Coulson Place', '2', 'Norlane', 'VIC', 3214, 1,
     '150 CM Folding Dining Table', 'TFS-COW-13579620-KC59367NA-New', '7FXCB6PW', '', '+61438757931',
     0, '7FXCB6PW', 'AUTFS', 'AU', '30-10-2026', '7FXCB6PW', '230.490000'],
    ['2026-11-10T00:00:00', 'Jason Oakley', '1 Main St', '', 'Taree', 'NSW', '2430', 1,
     'Item A', 'TFS-KAZ-6SM7HB0929-New', 'TJPQ6RDG', '', '0400000000', 0, 'TJPQ6RDG', 'AUTFS', 'AU',
     '07-11-2026', 'TJPQ6RDG', '59.210000'],
    ['2026-11-10T00:00:00', 'Jason Oakley', '1 Main St', '', 'Taree', 'NSW', '2430', 1,
     'Item B', 'TFS-KAZ-55HZDB07WV-New', 'TJPQ6RDG', '', '0400000000', 1, 'TJPQ6RDG', 'AUTFS', 'AU',
     '07-11-2026', 'TJPQ6RDG', '32.980000'],
    ['2026-11-10T00:00:00', 'Paul Carter', '12A Grey Gum Road', '', 'Denman', 'NSW', '2328', 3,
     'Gravel Grid', 'TFS-XVR-JDWG2INCH1644S7N7001V0-New', 'Q9F32X9C', '', '0419126108', 0, 'Q9F32X9C',
     'AUTFS', 'AU', '06-11-2026', 'Q9F32X9C', '91.780000'],
]

BIGW_CSV = (
    'Order No,Purchased Date,Product Name,SKU,Name,Company Name,Address,Address 2,Suburb,Postcode,State,'
    'Phone,Option,Quantity,Price(Per Unit),Total Shipping,Total Sale Price,Commission Fee,Shipping Area Paid,'
    'Payments,Tracking Code,Courier,New Supplier Note,All Supplier Notes,Order Group No,Combined Order,Status,Source\n'
    '1023584140,10/11/2026,Huggies Nappies,PPCO-11360-New,Liz Cotter,,331 Northern Road,,ROMA,4455,Queensland,'
    '+61438844357,,2,96.6400,0,193.2800,31.9000,FREE,,,,,,361714417,No,Purchased,BigW\n'
    '1023815960,10/11/2026,Garden Arbor,NPP-525247166740-New,Liz Davies,,1 Roborough Ave,,MOUNT ELIZA,3930,'
    'Victoria,+61418441931,,1,298.6800,9.9900,308.6700,50.9300,FLAT RATE,,,,,,361720928,No,Purchased,BigW\n'
    '1022769920,09/11/2026,Ornaments,APP-HNC2LB0CG1-New,KIMBERLEE BANGLE,,28 Lingard St,,MEREWETHER,2291,NSW,'
    '0407632469,,1,51.2200,0,51.2200,8.4500,FREE,,,,,,361692136,No,Purchased,EverydayMarket\n'
    '1000000001,09/11/2026,Old order,APP-OLD-New,Someone,,1 St,,TOWN,2000,NSW,0400000000,,1,10.00,0,10.00,'
    '1.65,FREE,,,,,,1,No,Dispatched,BigW\n'
)


def kogan_xlsx(rows=KOGAN_ROWS, headers=KOGAN_HEADERS) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append(headers)
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ParseTests(TestCase):
    def test_kogan_groups_lines_by_order_id(self):
        parsed = parse_order_file('OpenOrders.xlsx', kogan_xlsx())
        self.assertEqual(parsed.file_format, 'kogan')
        self.assertEqual(len(parsed.orders), 3)
        self.assertEqual(parsed.line_count, 4)
        by_id = {o.order_id: o for o in parsed.orders}
        self.assertEqual(len(by_id['TJPQ6RDG'].lines), 2)
        q = by_id['Q9F32X9C']
        self.assertEqual(q.lines[0]['quantity'], 3)
        self.assertEqual(str(q.total), '275.34')  # ItemPrice is per unit
        first = by_id['7FXCB6PW']
        self.assertEqual(first.ordered_at, '2026-10-30')
        self.assertEqual(first.customer['shippingAddress']['postcode'], '3214')
        self.assertIsNone(first.commission)

    def test_bigw_keeps_commission_and_skips_closed_orders(self):
        parsed = parse_order_file('PendingOrdersExport.csv', BIGW_CSV.encode())
        self.assertEqual(parsed.file_format, 'bigw')
        self.assertEqual(len(parsed.orders), 3)
        self.assertEqual(len(parsed.skipped), 1)
        by_id = {o.order_id: o for o in parsed.orders}
        nappies = by_id['1023584140']
        self.assertEqual(str(nappies.subtotal), '193.28')
        self.assertEqual(str(nappies.commission), '31.90')
        arbor = by_id['1023815960']
        self.assertEqual(str(arbor.shipping), '9.99')
        self.assertEqual(str(arbor.total), '308.67')
        self.assertEqual(by_id['1022769920'].marketplace, 'EverydayMarket')
        self.assertEqual(nappies.ordered_at, '2026-11-10')

    def test_unknown_file_rejected(self):
        with self.assertRaisesMessage(OrderFileError, 'not a Kogan or BigW'):
            parse_order_file('x.csv', b'a,b,c\n1,2,3\n')

    def test_missing_price_column_rejected(self):
        headers = [h for h in KOGAN_HEADERS if h != 'ItemPrice']
        rows = [r[:-1] for r in KOGAN_ROWS]
        with self.assertRaisesMessage(OrderFileError, 'missing columns: ItemPrice'):
            parse_order_file('k.xlsx', kogan_xlsx(rows, headers))

    def test_bad_quantity_names_the_row(self):
        rows = [list(KOGAN_ROWS[0])]
        rows[0][7] = 'two'
        with self.assertRaisesMessage(OrderFileError, 'Row 2: Quantity'):
            parse_order_file('k.xlsx', kogan_xlsx(rows))

    def test_wrong_extension_rejected(self):
        with self.assertRaisesMessage(OrderFileError, '.xlsx or .csv'):
            parse_order_file('orders.pdf', b'%PDF')


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class ImportApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ofi', email='ofi@example.com', password='pass12345')
        self.kogan, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.mydeal, _ = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _store(self, mp, vendor='costco_au', name='Kogan-TFS'):
        return Store.objects.create(
            user=self.user, name=name, region='AU', api_token='t', marketplace=mp, auto_buy_vendor=vendor,
        )

    def _upload(self, store, name, data):
        return self.client.post(
            f'/api/v1/stores/{store.id}/orders/import-file/',
            {'file': SimpleUploadedFile(name, data)},
            format='multipart',
        )

    def test_kogan_import_creates_orders_shown_in_order_layout(self):
        store = self._store(self.kogan)
        res = self._upload(store, 'OpenOrders 1.xlsx', kogan_xlsx())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['orders'], res.data['created'], res.data['updated']), (3, 3, 0))

        listing = self.client.get(f'/api/v1/stores/{store.id}/orders/')
        self.assertEqual(listing.status_code, 200)
        orders = {o['invoice_number']: o for o in listing.data['orders']}
        o = orders['TJPQ6RDG']
        self.assertEqual(o['status'], OrderStatus.PAID)
        self.assertEqual(o['total_amount_cents'], 9219)
        d = o['details']
        self.assertEqual(d['customer']['name'], 'Jason Oakley')
        self.assertEqual(d['shippingAddress']['city'], 'Taree')
        self.assertEqual([li['sku'] for li in d['lineItems']], ['TFS-KAZ-6SM7HB0929-New', 'TFS-KAZ-55HZDB07WV-New'])
        self.assertEqual(d['lineItems'][0]['priceCents'], 5921)
        self.assertEqual(d['totals']['totalCents'], 9219)
        self.assertEqual(d['dates']['orderedAt'], '2026-11-07')
        q = orders['Q9F32X9C']['details']
        self.assertEqual((q['lineItems'][0]['quantity'], q['lineItems'][0]['priceCents']), (3, 9178))
        self.assertEqual(q['totals']['totalCents'], 27534)

    def test_reimport_updates_without_duplicates_or_status_change(self):
        store = self._store(self.kogan)
        self._upload(store, 'a.xlsx', kogan_xlsx())
        MarketplaceOrder.objects.filter(store=store, invoice_number='7FXCB6PW').update(
            status=OrderStatus.SHIPPING_SUBMITTED,
        )
        rows = [list(r) for r in KOGAN_ROWS]
        rows[0][1] = 'Sheryl R Raymer'
        res = self._upload(store, 'b.xlsx', kogan_xlsx(rows))
        self.assertEqual((res.data['created'], res.data['updated']), (0, 3))
        self.assertEqual(MarketplaceOrder.objects.filter(store=store).count(), 3)
        o = MarketplaceOrder.objects.get(store=store, invoice_number='7FXCB6PW')
        self.assertEqual(o.status, OrderStatus.SHIPPING_SUBMITTED)
        self.assertEqual(o.customer_info_json['name'], 'Sheryl R Raymer')

    def test_bigw_import_into_mydeal_store_keeps_fee(self):
        store = self._store(self.mydeal, name='BW-P&P')
        res = self._upload(store, 'PendingOrdersExport.csv', BIGW_CSV.encode())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['orders'], len(res.data['skipped'])), (3, 1))
        o = MarketplaceOrder.objects.get(store=store, invoice_number='1023584140')
        self.assertEqual(o.raw_response_json['commissionFeeCents'], 3190)
        self.assertEqual(o.line_items_json[0]['commissionCents'], 3190)
        self.assertEqual(o.total_amount_cents, 19328)

    def test_file_must_match_store_marketplace(self):
        store = self._store(self.kogan)
        res = self._upload(store, 'PendingOrdersExport.csv', BIGW_CSV.encode())
        self.assertEqual(res.status_code, 400)
        self.assertIn('BigW orders file', res.data['detail'])
        self.assertFalse(MarketplaceOrder.objects.filter(store=store).exists())

    def test_store_must_be_auto_purchase(self):
        store = self._store(self.kogan, vendor='')
        res = self._upload(store, 'a.xlsx', kogan_xlsx())
        self.assertEqual(res.status_code, 400)
        self.assertIn('Auto buying vendor', res.data['detail'])

    def test_bad_file_returns_message(self):
        store = self._store(self.kogan)
        res = self._upload(store, 'a.csv', b'x,y\n1,2\n')
        self.assertEqual(res.status_code, 400)
        self.assertIn('not a Kogan or BigW', res.data['detail'])

    def test_other_users_store_not_found(self):
        other = User.objects.create_user(username='other', email='o@example.com', password='pass12345')
        store = Store.objects.create(
            user=other, name='Theirs', region='AU', api_token='t', marketplace=self.kogan,
            auto_buy_vendor='amazon_au',
        )
        res = self._upload(store, 'a.xlsx', kogan_xlsx())
        self.assertEqual(res.status_code, 404)
