# Structured Lasoo variant options (up to 4 name/value pairs) + variation image.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0008_storelisting_options'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storelisting',
            name='options',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Combined Options summary (e.g. Size=XL; Color=Blue). Prefer option_1..4 fields.',
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_1_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_1_value',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_2_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_2_value',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_3_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_3_value',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_4_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='option_4_value',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='variation_image_url',
            field=models.CharField(blank=True, default='', max_length=1000),
        ),
    ]
