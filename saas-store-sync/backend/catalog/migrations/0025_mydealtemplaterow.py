import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0018_store_mydeal_fields'),
        ('catalog', '0024_productmapping_lookup_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='MydealTemplateRow',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('price', 'Price'), ('inventory', 'Inventory')], db_index=True, max_length=16)),
                ('row_number', models.PositiveIntegerField()),
                ('deal_id', models.CharField(blank=True, default='', max_length=64)),
                ('variant_id', models.CharField(blank=True, default='', max_length=64)),
                ('external_id', models.CharField(blank=True, default='', max_length=255)),
                ('sku', models.CharField(db_index=True, max_length=255)),
                ('options', models.TextField(blank=True, default='')),
                ('deal_title', models.TextField(blank=True, default='')),
                ('discontinued', models.CharField(blank=True, default='', max_length=16)),
                ('mydeal_approved', models.CharField(blank=True, default='', max_length=32)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mydeal_template_rows', to='stores.store')),
            ],
            options={
                'db_table': 'catalog_mydealtemplaterow',
                'ordering': ['row_number'],
                'indexes': [models.Index(fields=['store', 'kind', 'sku'], name='cat_mydeal_st_kind_sku')],
            },
        ),
        migrations.AddConstraint(
            model_name='mydealtemplaterow',
            constraint=models.UniqueConstraint(fields=('store', 'kind', 'row_number'), name='uq_mydeal_template_store_kind_row'),
        ),
    ]
