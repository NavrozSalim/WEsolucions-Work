from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0012_listingupload_async_ingest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storelisting',
            name='action',
            field=models.CharField(
                choices=[
                    ('create', 'Create'),
                    ('mapped', 'Mapped'),
                    ('delete', 'Delete from marketplace'),
                    ('delete_system', 'Delete from system'),
                ],
                default='create',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='listingupload',
            name='action',
            field=models.CharField(
                choices=[
                    ('create', 'Create'),
                    ('mapped', 'Mapped'),
                    ('delete', 'Delete from marketplace'),
                    ('delete_system', 'Delete from system'),
                ],
                default='create',
                max_length=20,
            ),
        ),
    ]
