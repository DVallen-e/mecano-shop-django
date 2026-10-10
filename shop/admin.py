"""Django Admin configuration for catalog, order operations, and payment audits."""

from datetime import timedelta
import logging

from django.contrib import admin
from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Announcement,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductCharacteristic,
    StripeWebhookEvent,
    Tag,
)

logger = logging.getLogger(__name__)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("text", "active")
    list_filter = ("active",)

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("text",)


class ProductCharacteristicInline(admin.TabularInline):
    model = ProductCharacteristic
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "price",
        "stock_quantity",
    )

    prepopulated_fields = {
        "slug": ("name",),
    }

    inlines = (ProductCharacteristicInline,)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "unit_price", "quantity")
    can_delete = False


class InsufficientStock(Exception):
    """Abort an admin approval transaction when an order cannot be reserved."""

    pass


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Expose order history while routing inventory changes through safe actions."""

    list_display = ("id", "user", "status", "payment_status", "total", "created_at")
    list_filter = ("status", "payment_status")
    search_fields = ("id", "email", "first_name", "last_name")
    inlines = (OrderItemInline,)
    readonly_fields = (
        "user",
        "first_name",
        "last_name",
        "email",
        "phone",
        "shipping_name",
        "shipping_address_line1",
        "shipping_address_line2",
        "shipping_city",
        "shipping_postal_code",
        "shipping_country",
        "status",
        "payment_status",
        "subtotal",
        "transport_fee",
        "total",
        "currency",
        "inventory_reserved",
        "created_at",
        "updated_at",
    )
    actions = (
        "approve_for_payment",
        "cancel_orders",
        "expire_stale_checkout_sessions",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve selected orders for payment")
    def approve_for_payment(self, request, queryset):
        """Reserve every order's stock atomically before marking it payable."""
        approved = 0
        for order_id in queryset.values_list("id", flat=True):
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order_id)
                    if order.status != Order.Status.AWAITING_REVIEW:
                        raise InsufficientStock("Order is no longer awaiting review.")

                    items = list(
                        order.items.select_related("product").order_by("product_id")
                    )
                    if not items or any(item.product is None for item in items):
                        raise InsufficientStock("Order contains an unavailable product.")

                    for item in items:
                        updated = Product.objects.filter(
                            pk=item.product_id,
                            stock_quantity__gte=item.quantity,
                        ).update(
                            stock_quantity=F("stock_quantity") - item.quantity
                        )
                        if updated != 1:
                            raise InsufficientStock(
                                f"Insufficient stock for {item.product_name}."
                            )

                    order.status = Order.Status.AWAITING_PAYMENT
                    order.inventory_reserved = True
                    order.save(update_fields=(
                        "status",
                        "inventory_reserved",
                        "updated_at",
                    ))
                    approved += 1
            except InsufficientStock as error:
                self.message_user(
                    request,
                    f"Order #{order_id} was not approved: {error}",
                    level=messages.ERROR,
                )

        if approved:
            self.message_user(
                request,
                f"{approved} order(s) approved and stock reserved.",
                level=messages.SUCCESS,
            )

    @admin.action(description="Cancel selected unpaid or failed orders")
    def cancel_orders(self, request, queryset):
        """Cancel eligible unpaid orders and return any reserved quantity to stock."""
        cancelled = 0
        for order_id in queryset.values_list("id", flat=True):
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id)
                if (
                    order.status not in (
                        Order.Status.AWAITING_REVIEW,
                        Order.Status.AWAITING_PAYMENT,
                    )
                    or order.payment_status not in (
                        Order.PaymentStatus.UNPAID,
                        Order.PaymentStatus.FAILED,
                    )
                ):
                    continue

                if order.inventory_reserved:
                    items = list(order.items.all())
                    for item in items:
                        if item.product_id is not None:
                            Product.objects.filter(pk=item.product_id).update(
                                stock_quantity=F("stock_quantity") + item.quantity
                            )
                    order.inventory_reserved = False

                order.status = Order.Status.CANCELLED
                order.save(update_fields=("status", "inventory_reserved", "updated_at"))
                cancelled += 1

        if cancelled:
            self.message_user(
                request,
                f"{cancelled} unpaid order(s) cancelled.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No eligible unpaid orders were selected.",
                level=messages.WARNING,
            )

    @admin.action(
        description="Expire Stripe sessions pending over 10 hours and release stock"
    )
    def expire_stale_checkout_sessions(self, request, queryset):
        """Expire pending Checkout Sessions older than ten hours via Stripe.

        Inventory is released only after Stripe reports ``expired`` and a locked
        database recheck confirms the order and attempt are still pending. API
        errors, completed sessions, and concurrent webhook updates leave stock
        untouched. The order and attempt rows are retained for audit history.
        """
        if not settings.STRIPE_SECRET_KEY:
            self.message_user(
                request,
                "Stripe is not configured; no sessions were changed.",
                level=messages.ERROR,
            )
            return

        try:
            import stripe
        except ImportError:
            logger.exception("Stripe SDK is not installed.")
            self.message_user(
                request,
                "Stripe is unavailable; no sessions were changed.",
                level=messages.ERROR,
            )
            return

        stripe.api_key = settings.STRIPE_SECRET_KEY
        cutoff = timezone.now() - timedelta(hours=10)
        expired = 0
        skipped = 0
        failed = 0
        order_ids = queryset.values_list("id", flat=True)

        for order_id in order_ids:
            order = Order.objects.filter(
                pk=order_id,
                status=Order.Status.AWAITING_PAYMENT,
                payment_status=Order.PaymentStatus.PENDING,
            ).first()
            if order is None:
                skipped += 1
                continue

            attempt = (
                order.payment_attempts.filter(
                    status=PaymentAttempt.Status.PENDING,
                    checkout_session_id__isnull=False,
                    created_at__lte=cutoff,
                )
                .exclude(checkout_session_id="")
                .order_by("-created_at")
                .first()
            )
            if attempt is None:
                skipped += 1
                continue

            try:
                session = stripe.checkout.Session.retrieve(
                    attempt.checkout_session_id
                )
                if session.status == "open":
                    session = stripe.checkout.Session.expire(
                        attempt.checkout_session_id
                    )
            except stripe.StripeError:
                logger.exception(
                    "Could not expire Stripe Checkout Session for order %s.",
                    order_id,
                )
                failed += 1
                continue

            if session.status != "expired":
                skipped += 1
                continue

            with transaction.atomic():
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                attempt = PaymentAttempt.objects.select_for_update().filter(
                    pk=attempt.pk
                ).first()
                if (
                    order is None
                    or attempt is None
                    or order.status != Order.Status.AWAITING_PAYMENT
                    or order.payment_status != Order.PaymentStatus.PENDING
                    or attempt.status != PaymentAttempt.Status.PENDING
                    or attempt.checkout_session_id != session.id
                    or attempt.created_at > cutoff
                ):
                    skipped += 1
                    continue

                if order.inventory_reserved:
                    for item in order.items.all():
                        if item.product_id is not None:
                            Product.objects.filter(pk=item.product_id).update(
                                stock_quantity=F("stock_quantity") + item.quantity
                            )
                    order.inventory_reserved = False

                attempt.status = PaymentAttempt.Status.CANCELLED
                attempt.last_error_code = "session_expired"
                attempt.save(update_fields=(
                    "status",
                    "last_error_code",
                    "updated_at",
                ))
                order.payment_status = Order.PaymentStatus.FAILED
                order.save(update_fields=(
                    "payment_status",
                    "inventory_reserved",
                    "updated_at",
                ))
                expired += 1

        if expired:
            self.message_user(
                request,
                f"{expired} Stripe session(s) expired; reserved stock released.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} order(s) skipped because they were not eligible "
                "or Stripe reported a non-expired session.",
                level=messages.WARNING,
            )
        if failed:
            self.message_user(
                request,
                f"{failed} Stripe session(s) could not be checked or expired; "
                "stock was not released.",
                level=messages.ERROR,
            )


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    """Expose payment attempts for inspection without allowing manual edits."""

    list_display = ("id", "order", "status", "amount", "currency", "created_at")
    list_filter = ("status", "currency")
    readonly_fields = (
        "order",
        "status",
        "last_error_code",
        "checkout_session_id",
        "payment_intent_id",
        "amount",
        "currency",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    """Keep a read-only event ledger for webhook troubleshooting and audits."""

    list_display = ("event_id", "event_type", "order", "processed_at", "created_at")
    list_filter = ("event_type",)
    search_fields = ("event_id", "event_type")
    readonly_fields = ("event_id", "event_type", "order", "processed_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False