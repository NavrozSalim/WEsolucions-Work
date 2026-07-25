from django.db import migrations, models
import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0024_nora_inventory_file'),
    ]

    operations = [
        migrations.AlterField(
            model_name='store',
            name='mydeal_setup_method',
            field=models.CharField(
                blank=True,
                choices=[('upload', 'Upload templates'), ('api', 'API connection')],
                default='upload',
                help_text='MyDeal (Woolworths/WMP): upload Price/Inventory CSV templates or Universal API.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_environment',
            field=models.CharField(
                blank=True,
                choices=[('sandbox', 'Sandbox'), ('production', 'Production (Live)')],
                default='sandbox',
                help_text='Which MyDeal environment listing pushes / order pulls use by default.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_sandbox_base_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_production_base_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_sandbox_client_id',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_sandbox_client_secret',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_sandbox_seller_id',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_sandbox_seller_token',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_production_client_id',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_production_client_secret',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_production_seller_id',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='mydeal_production_seller_token',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
    ]
