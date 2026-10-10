"""Adds the shipping address snapshot collected by Stripe Checkout."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0005_orders_inventory_payments"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="shipping_name",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_address_line1",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_address_line2",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_city",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_postal_code",
            field=models.CharField(blank=True, default="", max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_country",
            field=models.CharField(blank=True, default="", max_length=2),
            preserve_default=False,
        ),
    ]
