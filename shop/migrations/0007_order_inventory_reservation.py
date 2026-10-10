"""Tracks stock reservations and marks existing awaiting-payment orders reserved."""

from django.db import migrations, models


def mark_existing_reservations(apps, schema_editor):
    """Preserve inventory state for orders created before the tracking field."""
    Order = apps.get_model("shop", "Order")
    database = schema_editor.connection.alias
    Order.objects.using(database).filter(status="awaiting_payment").update(
        inventory_reserved=True
    )


class Migration(migrations.Migration):
    """Add reservation state and backfill legacy orders without losing stock holds."""

    dependencies = [
        ("shop", "0006_order_shipping_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="inventory_reserved",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            mark_existing_reservations,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("awaiting_review", "Awaiting workshop review"),
                    ("awaiting_payment", "Awaiting payment"),
                    ("paid", "Paid"),
                    ("processing", "Processing"),
                    ("shipped", "Shipped"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="awaiting_payment",
                max_length=20,
            ),
        ),
    ]
