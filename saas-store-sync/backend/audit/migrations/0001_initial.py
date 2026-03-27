# Generated migration for audit app

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(max_length=64)),
                ('object_type', models.CharField(max_length=64)),
                ('object_id', models.CharField(max_length=255)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-timestamp'],
                'verbose_name': 'Audit log',
                'verbose_name_plural': 'Audit logs',
            },
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['user', '-timestamp'], name='audit_audit_user_id_7b2b0d_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['object_type', 'object_id'], name='audit_audit_object__a8e0a0_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['-timestamp'], name='audit_audit_timesta_9c1e2a_idx'),
        ),
    ]
