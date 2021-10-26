# Generated by Django 3.2.8 on 2021-10-24 08:47

from django.db import migrations, models
import django.db.models.deletion
import modeltrans.fields
import paths.utils


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='InstanceConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier', paths.utils.IdentifierField(max_length=50, validators=[paths.utils.IdentifierValidator()], verbose_name='identifier')),
                ('name', models.CharField(max_length=150, null=True, verbose_name='name')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('modified_at', models.DateTimeField(auto_now=True)),
                ('i18n', modeltrans.fields.TranslationField(fields=('name',), required_languages=(), virtual_fields=True)),
            ],
            options={
                'verbose_name': 'Instance',
                'verbose_name_plural': 'Instances',
            },
        ),
        migrations.CreateModel(
            name='InstanceHostname',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hostname', models.CharField(max_length=100, unique=True)),
                ('base_path', models.CharField(blank=True, max_length=100, null=True)),
                ('instance', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hostnames', to='nodes.instanceconfig')),
            ],
            options={
                'verbose_name': 'Instance hostname',
                'verbose_name_plural': 'Instance hostnames',
            },
        ),
        migrations.CreateModel(
            name='NodeConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier', paths.utils.IdentifierField(max_length=50, validators=[paths.utils.IdentifierValidator()], verbose_name='identifier')),
                ('name', models.CharField(blank=True, max_length=200, null=True)),
                ('color', models.CharField(blank=True, max_length=20, null=True)),
                ('forecast_values', models.JSONField(null=True)),
                ('historical_values', models.JSONField(null=True)),
                ('params', models.JSONField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('modified_at', models.DateTimeField(auto_now=True)),
                ('i18n', modeltrans.fields.TranslationField(fields=('name',), required_languages=(), virtual_fields=True)),
                ('instance', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nodes', to='nodes.instanceconfig')),
            ],
            options={
                'verbose_name': 'Node',
                'verbose_name_plural': 'Nodes',
                'unique_together': {('instance', 'identifier')},
            },
        ),
    ]