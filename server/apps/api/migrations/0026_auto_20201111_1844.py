# Generated by Django 3.0.7 on 2020-11-11 18:44

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_clothes_category'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='categorydata',
            name='clothes',
        ),
        migrations.RemoveField(
            model_name='clothes',
            name='category',
        ),
        migrations.AddField(
            model_name='clothes',
            name='category_num',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='api.CategoryData'),
        ),
        migrations.AlterField(
            model_name='categorydata',
            name='lower_category',
            field=models.CharField(max_length=18),
        ),
        migrations.AlterField(
            model_name='categorydata',
            name='upper_category',
            field=models.CharField(max_length=9),
        ),
    ]