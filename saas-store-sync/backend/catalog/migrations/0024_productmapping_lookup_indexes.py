# Composite indexes for hot paths: _find_product_mapping (store + is_active + SKU/id)
# and upload-scrape row filters. PostgreSQL uses CREATE INDEX CONCURRENTLY (no long
# ACCESS EXCLUSIVE lock). SQLite (CI) uses schema_editor.add_index inside a transaction.

from django.db import migrations, models


def _forwards_indexes(apps, schema_editor):
    ProductMapping = apps.get_model('catalog', 'ProductMapping')
    connection = schema_editor.connection
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS cat_pm_st_act_mcsku
                ON catalog_productmapping (store_id, is_active, marketplace_child_sku);
                """
            )
            cursor.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS cat_pm_st_act_mpsku
                ON catalog_productmapping (store_id, is_active, marketplace_parent_sku);
                """
            )
            cursor.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS cat_pm_st_act_mid
                ON catalog_productmapping (store_id, is_active, marketplace_id);
                """
            )
    else:
        schema_editor.add_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_child_sku'],
                name='cat_pm_st_act_mcsku',
            ),
        )
        schema_editor.add_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_parent_sku'],
                name='cat_pm_st_act_mpsku',
            ),
        )
        schema_editor.add_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_id'],
                name='cat_pm_st_act_mid',
            ),
        )


def _reverse_indexes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX IF EXISTS cat_pm_st_act_mcsku;')
            cursor.execute('DROP INDEX IF EXISTS cat_pm_st_act_mpsku;')
            cursor.execute('DROP INDEX IF EXISTS cat_pm_st_act_mid;')
    else:
        ProductMapping = apps.get_model('catalog', 'ProductMapping')
        schema_editor.remove_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_child_sku'],
                name='cat_pm_st_act_mcsku',
            ),
        )
        schema_editor.remove_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_parent_sku'],
                name='cat_pm_st_act_mpsku',
            ),
        )
        schema_editor.remove_index(
            ProductMapping,
            models.Index(
                fields=['store', 'is_active', 'marketplace_id'],
                name='cat_pm_st_act_mid',
            ),
        )


class Migration(migrations.Migration):
    # Required: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    atomic = False

    dependencies = [
        ('catalog', '0023_productmapping_store_active_sync_idx'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='productmapping',
                    index=models.Index(
                        fields=['store', 'is_active', 'marketplace_child_sku'],
                        name='cat_pm_st_act_mcsku',
                    ),
                ),
                migrations.AddIndex(
                    model_name='productmapping',
                    index=models.Index(
                        fields=['store', 'is_active', 'marketplace_parent_sku'],
                        name='cat_pm_st_act_mpsku',
                    ),
                ),
                migrations.AddIndex(
                    model_name='productmapping',
                    index=models.Index(
                        fields=['store', 'is_active', 'marketplace_id'],
                        name='cat_pm_st_act_mid',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(_forwards_indexes, _reverse_indexes, atomic=False),
            ],
        ),
    ]
