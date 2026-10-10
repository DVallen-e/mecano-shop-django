"""Stores the latest Stripe payment error code for customer/admin support."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add a non-required diagnostic field to each payment attempt."""

    dependencies = [
        ("shop", "0007_order_inventory_reservation"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentattempt",
            name="last_error_code",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
    ]
