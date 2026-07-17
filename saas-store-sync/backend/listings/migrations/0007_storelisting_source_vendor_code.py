from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0006_storelisting_vendor_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='source_vendor_code',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Canonical source vendor code from the Vendor Name template column.',
                max_length=50,
            ),
        ),
    ]
