from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0031_store_temu_credentials'),
    ]

    operations = [
        migrations.AlterField(
            model_name='store',
            name='temu_region',
            field=models.CharField(
                blank=True,
                choices=[('au', 'Australia / Global'), ('us', 'United States')],
                default='au',
                help_text='Temu API region, copied from the store Region. AU uses openapi-b-global.temu.com; USA uses openapi-b-us.temu.com.',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='store',
            name='temu_base_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='Override the Temu Open API host. Blank uses the host for the store Region.',
                max_length=500,
            ),
        ),
    ]
