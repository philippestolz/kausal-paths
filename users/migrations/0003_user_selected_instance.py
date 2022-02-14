# Generated by Django 3.2.9 on 2021-11-18 17:05

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('nodes', '0004_add_fields'),
        ('users', '0002_rename_user_table'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='selected_instance',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='nodes.instanceconfig'),
        ),
    ]