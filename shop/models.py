
"""Persistent catalog, order, inventory, and payment records for the shop."""

from decimal import Decimal

from django.conf import settings
from django.db import models


# Keep shipping rules here so cart display and order creation use the same values.
FREE_SHIPPING_THRESHOLD = Decimal("120.00")
STANDARD_SHIPPING_FEE = Decimal("7.00")


class Announcement(models.Model):
    text = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"

    def __str__(self):
        return self.text

class Tag(models.Model):
    text = models.CharField(max_length=50)

    def __str__(self):
        return self.text
    


class Product(models.Model):
    """A catalog product and its current available stock.

    Checkout reserves stock by decrementing stock_quantity. A confirmed
    cancellation or expired payment restores the reserved quantity.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    tags = models.ManyToManyField(Tag, blank=True)

    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    stock_quantity = models.PositiveIntegerField(default=0)

    image = models.ImageField(
        upload_to="products/",
        blank=True,
        null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Order(models.Model):
    """Checkout snapshot and lifecycle state belonging to one customer.

    Order.status tracks fulfillment; payment_status tracks the separate payment
    lifecycle. inventory_reserved prevents releasing the same stock twice.
    """

    class Status(models.TextChoices):
        AWAITING_REVIEW = "awaiting_review", "Awaiting workshop review"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        PAID = "paid", "Paid"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    shipping_name = models.CharField(max_length=255, blank=True)
    shipping_address_line1 = models.CharField(max_length=255, blank=True)
    shipping_address_line2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_postal_code = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=2, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AWAITING_PAYMENT,
    )
    payment_status = models.CharField(
        max_length=12,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    transport_fee = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    inventory_reserved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Order #{self.pk}"


class OrderItem(models.Model):
    """Immutable purchase-time name/price snapshot for one product line."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    product_name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="order_item_quantity_positive",
            ),
        ]

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"


class PaymentAttempt(models.Model):
    """One Stripe Checkout attempt; retries create new attempts for the same order.

    Store both Stripe IDs so Checkout and PaymentIntent webhook events can be
    matched to this record and audited without retaining sensitive card details.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payment_attempts",
    )
    checkout_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    last_error_code = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payment attempt for order #{self.order_id}"


class StripeWebhookEvent(models.Model):
    """Processed Stripe event IDs make webhook delivery safe to retry idempotently."""

    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stripe_events",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"


class ProductCharacteristic(models.Model):
    """Admin-managed product details displayed on the product page."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="characteristics",
    )
    name = models.CharField(max_length=100)
    value = models.TextField()

    class Meta:
        ordering = ("id",)
        verbose_name = "Product characteristic"
        verbose_name_plural = "Product characteristics"

    def __str__(self):
        return f"{self.name}: {self.value}"