# Generated by Django 3.2.16 on 2023-01-26 04:21

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import modelcluster.fields


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('datasets', '0003_allow_unattached_comments'),
    ]

    operations = [
        migrations.AddField(
            model_name='datasetcomment',
            name='resolved_at',
            field=models.DateTimeField(editable=False, null=True, verbose_name='resolved at'),
        ),
        migrations.AddField(
            model_name='datasetcomment',
            name='resolved_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_comments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='datasetcomment',
            name='state',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='datasetcomment',
            name='type',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name='dimensioncategory',
            name='dimension',
            field=modelcluster.fields.ParentalKey(on_delete=django.db.models.deletion.CASCADE, related_name='categories', to='datasets.dimension'),
        ),
    ]
