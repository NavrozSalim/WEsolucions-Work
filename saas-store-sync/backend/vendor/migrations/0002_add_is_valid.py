from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='googleoauthcredentials',
            name='is_valid',
            field=models.BooleanField(default=True),
        ),
    ]
