from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0012_storepricerangemargin_margin_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storepricerangemargin',
            name='margin_type',
            field=models.CharField(
                choices=[
                    ('percentage', 'Percentage markup'),
                    ('fixed', 'Fixed dollar add-on'),
                    ('direct', 'Direct multiplier'),
                ],
                default='percentage',
                help_text='percentage: price = cost_after_tax × (1 + value/100). fixed: price = cost_after_tax + value.',
                max_length=20,
            ),
        ),
    ]
