from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0011_alter_storepricerangemargin_margin_percentage'),
    ]

    operations = [
        migrations.AddField(
            model_name='storepricerangemargin',
            name='margin_type',
            field=models.CharField(
                choices=[('percentage', 'Percentage markup'), ('fixed', 'Fixed dollar add-on')],
                default='percentage',
                help_text='percentage: price = cost_after_tax × (1 + value/100). fixed: price = cost_after_tax + value.',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='storepricerangemargin',
            name='margin_percentage',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Meaning depends on margin_type: percentage points (e.g. 25 = +25%%) or fixed USD amount.',
                max_digits=10,
            ),
        ),
    ]
