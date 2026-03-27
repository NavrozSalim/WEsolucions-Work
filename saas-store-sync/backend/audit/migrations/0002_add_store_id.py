from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0006_remove_platform'),
        ('audit', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='store',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='audit_logs',
                to='stores.store',
                db_index=True,
            ),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['store', '-timestamp'], name='idx_audit_store_ts'),
        ),
    ]
