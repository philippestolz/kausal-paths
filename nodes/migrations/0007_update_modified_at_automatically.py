# Generated by Django 3.2.9 on 2022-01-21 14:47

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('nodes', '0006_auto_20211102_0912'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instanceconfig',
            name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name='nodeconfig',
            name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
