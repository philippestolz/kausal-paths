# Generated by Django 3.2.16 on 2023-02-01 07:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('datasets', '0005_alter_dataset_options'),
    ]

    operations = [
        migrations.RenameField(
            model_name='datasetcomment',
            old_name='row_uuid',
            new_name='cell_uuid',
        ),
        migrations.AlterField(
            model_name='datasetcomment',
            name='state',
            field=models.CharField(blank=True, choices=[('resolved', 'Resolved'), ('unresolved', 'Unresolved')], max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name='datasetcomment',
            name='type',
            field=models.CharField(blank=True, choices=[('review', 'Review comment'), ('sticky', 'Sticky comment')], max_length=20, null=True),
        ),
    ]