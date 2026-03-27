# Drop orders tables (orders app removed)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0004_delete_synclog'),
    ]

    operations = [
        # No CASCADE: SQLite does not support it on DROP TABLE; PG is fine without for these tables.
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS orders_orderitem;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS orders_order;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
