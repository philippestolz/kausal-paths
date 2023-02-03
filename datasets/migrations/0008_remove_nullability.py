# Generated by Django 3.2.16 on 2023-02-03 07:24

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('datasets', '0007_use_cell_path_for_comments'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dataset',
            name='table',
            field=models.JSONField(),
        ),
        migrations.AlterField(
            model_name='dataset',
            name='years',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), size=None),
        ),
    ]
