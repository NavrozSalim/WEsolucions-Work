from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0017_store_catalog_zero_pending_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='mydeal_profile',
            field=models.CharField(
                blank=True,
                choices=[('TFS', 'TFS'), ('P&P', 'P&P')],
                help_text='Which Mydeal CSV template family this store uses (TFS or P&P).',
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='storevendorpricesettings',
            name='mydeal_rrp_margin_percentage',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Mydeal RRP margin %: RRP(IncGST) = Price(IncGST) / (margin/100).',
                max_digits=6,
                null=True,
            ),
        ),
    ]
