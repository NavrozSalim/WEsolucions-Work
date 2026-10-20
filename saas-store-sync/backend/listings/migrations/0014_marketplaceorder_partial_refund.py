from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0013_listing_action_delete_system'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketplaceorder',
            name='refunded_amount_cents',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='marketplaceorder',
            name='partial_refunds_json',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
