from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('stores', '0006_remove_platform'),
        ('products', '0005_consolidate_and_remove_store'),
    ]

    operations = [
        migrations.AddField(
            model_name='upload',
            name='user',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='uploads',
                to=settings.AUTH_USER_MODEL,
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name='upload',
            name='store',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='uploads',
                to='stores.store',
                db_index=True,
            ),
        ),
    ]
