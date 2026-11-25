"""One running catalog scrape per store and vendor (was one per store).

The old table used the store as its primary key. Its rows move to a new table
keyed by an id, with one row allowed per (store, vendor); empty vendor = every
vendor. Running scrapes keep their run id (root_task_id), so their workers find
their row again once they run the new code.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


def copy_running_scrapes(apps, schema_editor):
    Legacy = apps.get_model('catalog', 'LegacyStoreCeleryScrapeState')
    Run = apps.get_model('catalog', 'StoreCatalogCeleryScrapeState')
    for old in Legacy.objects.all():
        run = Run.objects.create(
            store_id=old.store_id,
            scope=old.scope,
            upload_id=old.upload_id,
            root_task_id=old.root_task_id or '',
            cancel_requested=old.cancel_requested,
            cancel_requested_at=old.cancel_requested_at,
            first_worker_started_at=old.first_worker_started_at,
            scrape_vendor_id=(old.scrape_vendor_id or '').strip(),
            last_heartbeat_at=old.last_heartbeat_at,
        )
        # enqueued_at is auto_now_add: keep the original start time.
        Run.objects.filter(pk=run.pk).update(enqueued_at=old.enqueued_at)


def copy_back(apps, schema_editor):
    Legacy = apps.get_model('catalog', 'LegacyStoreCeleryScrapeState')
    Run = apps.get_model('catalog', 'StoreCatalogCeleryScrapeState')
    seen = set()
    for run in Run.objects.order_by('scrape_vendor_id'):
        if run.store_id in seen:
            continue  # the old table holds one scrape per store
        seen.add(run.store_id)
        Legacy.objects.create(
            store_id=run.store_id,
            scope=run.scope,
            upload_id=run.upload_id,
            root_task_id=run.root_task_id,
            cancel_requested=run.cancel_requested,
            cancel_requested_at=run.cancel_requested_at,
            first_worker_started_at=run.first_worker_started_at,
            scrape_vendor_id=run.scrape_vendor_id or None,
            last_heartbeat_at=run.last_heartbeat_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0041_auto_order_run_attention'),
        ('catalog', '0033_mydeal_upload_run'),
    ]

    operations = [
        # Keep the old table under another model name while its rows are copied.
        migrations.RenameModel('StoreCatalogCeleryScrapeState', 'LegacyStoreCeleryScrapeState'),
        migrations.AlterField(
            model_name='legacystoreceleryscrapestate',
            name='store',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                primary_key=True,
                related_name='legacy_catalog_scrape_state',
                serialize=False,
                to='stores.store',
            ),
        ),
        migrations.AlterField(
            model_name='legacystoreceleryscrapestate',
            name='upload',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='legacy_celery_scrape_states',
                to='catalog.catalogupload',
            ),
        ),
        migrations.CreateModel(
            name='StoreCatalogCeleryScrapeState',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('scope', models.CharField(
                    choices=[('store', 'Store-wide'), ('upload', 'Upload-scoped')], default='store', max_length=16,
                )),
                ('root_task_id', models.CharField(
                    blank=True, help_text='Celery task id of the enqueued top-level task (for debugging).',
                    max_length=255,
                )),
                ('cancel_requested', models.BooleanField(
                    default=False, help_text='User clicked Stop Scraping; worker loops should exit cooperatively.',
                )),
                ('cancel_requested_at', models.DateTimeField(
                    blank=True, help_text='When Stop was clicked; used to expire a stuck stopping state.', null=True,
                )),
                ('first_worker_started_at', models.DateTimeField(
                    blank=True,
                    help_text='Set when a worker first begins scraping rows; until then the job is only queued.',
                    null=True,
                )),
                ('enqueued_at', models.DateTimeField(auto_now_add=True)),
                ('scrape_vendor_id', models.CharField(
                    blank=True, default='',
                    help_text='Vendor the scrape is limited to; empty means every live-scrape vendor.', max_length=64,
                )),
                ('last_heartbeat_at', models.DateTimeField(
                    blank=True,
                    help_text=(
                        'Last time a worker reached the next listing. The watchdog restarts '
                        'a scrape that has gone quiet (worker restarted or stuck).'
                    ),
                    null=True,
                )),
                ('store', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='catalog_celery_scrape_states',
                    to='stores.store',
                )),
                ('upload', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='celery_scrape_states', to='catalog.catalogupload',
                )),
            ],
            options={
                'db_table': 'catalog_scraperun',
                'indexes': [models.Index(fields=['store', 'root_task_id'], name='catalog_scraperun_root')],
                'constraints': [
                    models.UniqueConstraint(fields=('store', 'scrape_vendor_id'), name='catalog_scraperun_one_per_vendor'),
                ],
            },
        ),
        migrations.RunPython(copy_running_scrapes, copy_back),
        migrations.DeleteModel(name='LegacyStoreCeleryScrapeState'),
    ]
