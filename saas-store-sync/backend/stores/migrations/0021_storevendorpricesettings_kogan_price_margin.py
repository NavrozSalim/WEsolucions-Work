from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0020_mydeal_setup_method'),
    ]

    operations = [
        migrations.AddField(
            model_name='storevendorpricesettings',
            name='kogan_price_margin_percentage',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Kogan only: PRICE column margin % off list price. PRICE = kogan_first_price / ((100 - this) / 100).',
                max_digits=6,
                null=True,
            ),
        ),
    ]
