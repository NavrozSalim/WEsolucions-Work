from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0014_marketplaceorder_partial_refund'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storelisting',
            name='action',
            field=models.CharField(
                choices=[
                    ('create', 'Create'),
                    ('mapped', 'Mapped'),
                    ('update', 'Update'),
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
                    ('update', 'Update'),
                    ('delete', 'Delete from marketplace'),
                    ('delete_system', 'Delete from system'),
                ],
                default='create',
                max_length=20,
            ),
        ),
    ]
