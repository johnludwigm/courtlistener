# Generated by Django 5.0 on 2024-04-05 16:33

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("scrapers", "0002_load_scraper_starting_point"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ErrorLog",
        ),
    ]
