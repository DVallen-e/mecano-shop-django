"""Adds numeric stock and the order, line-item, payment, and webhook ledger tables."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_stock_to_quantity(apps, schema_editor):
    Product = apps.get_model("shop", "Product")
    database = schema_editor.connection.alias
    Product.objects.using(database).filter(in_stock=True).update(stock_quantity=1)


def copy_quantity_to_stock(apps, schema_editor):
    Product = apps.get_model("shop", "Product")
    database = schema_editor.connection.alias
    for product in Product.objects.using(database).all().iterator():
        product.in_stock = product.stock_quantity > 0
        product.save(using=database, update_fields=("in_stock",))


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0004_productcharacteristic"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="stock_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(copy_stock_to_quantity, copy_quantity_to_stock),
        migrations.RemoveField(
            model_name="product",
            name="in_stock",
        ),
        migrations.CreateModel(
            name="Order",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "first_name",
                    models.CharField(max_length=150),
                ),
                (
                    "last_name",
                    models.CharField(max_length=150),
                ),
                ("email", models.EmailField(max_length=254)),
                ("phone", models.CharField(max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("awaiting_review", "Awaiting workshop review"),
                            ("awaiting_payment", "Awaiting payment"),
                            ("paid", "Paid"),
                            ("processing", "Processing"),
                            ("shipped", "Shipped"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="awaiting_review",
                        max_length=20,
                    ),
                ),
                (
                    "payment_status",
                    models.CharField(
                        choices=[
                            ("unpaid", "Unpaid"),
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("refunded", "Refunded"),
                        ],
                        default="unpaid",
                        max_length=12,
                    ),
                ),
                ("subtotal", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "transport_fee",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                ("total", models.DecimalField(decimal_places=2, max_digits=10)),
                ("currency", models.CharField(default="EUR", max_length=3)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="orders",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("product_name", models.CharField(max_length=200)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("quantity", models.PositiveIntegerField()),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="shop.order",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_items",
                        to="shop.product",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantity__gt", 0)),
                name="order_item_quantity_positive",
            ),
        ),
        migrations.CreateModel(
            name="PaymentAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "checkout_session_id",
                    models.CharField(blank=True, max_length=255, null=True, unique=True),
                ),
                (
                    "payment_intent_id",
                    models.CharField(blank=True, max_length=255, null=True, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("refunded", "Refunded"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("currency", models.CharField(default="EUR", max_length=3)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_attempts",
                        to="shop.order",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="StripeWebhookEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("event_id", models.CharField(max_length=255, unique=True)),
                ("event_type", models.CharField(max_length=100)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="stripe_events",
                        to="shop.order",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
    ]
