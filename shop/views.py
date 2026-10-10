"""Shop pages, order checkout, Stripe webhooks, and social sign-in endpoints.

The browser starts checkout, but only a verified Stripe webhook changes an order
to paid. Inventory is reserved in the database before redirecting to Stripe.
"""

import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Prefetch
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import CheckoutContactForm
from .models import (
    FREE_SHIPPING_THRESHOLD,
    STANDARD_SHIPPING_FEE,
    Announcement,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    StripeWebhookEvent,
    Tag,
)

User = get_user_model()
logger = logging.getLogger(__name__)


class StockUnavailable(Exception):
    """Raised to roll back checkout when inventory changes during reservation."""

    pass


def _safe_login_redirect(request, target):
    """Allow only same-host post-login redirects to prevent open redirects."""
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return reverse("profile")


def index(request):
    """Render the searchable, tag-filtered product catalog."""
    selected_tag = request.GET.get("tag")
    query = request.GET.get("search", "")
    announcement = Announcement.objects.filter(active=True).first()

    products = Product.objects.all()
    tags = Tag.objects.all()

    if selected_tag:
        products = products.filter(tags__text=selected_tag)

    if query:
        products = products.filter(name__icontains=query)

    return render(request, "index.html", {
        "products": products,
        "tags": tags,
        "selected_tag": selected_tag,
        "query": query,
        "announcement":announcement,
    })

def product_detail(request, slug):
    """Render one product with its tags and admin-managed characteristics."""
    product = get_object_or_404(
        Product.objects.prefetch_related("tags", "characteristics"),
        slug=slug,
    )

    return render(
        request,
        "product_detail.html",
        {"product": product},
    )

def add_to_cart(request, slug):
    """Add a validated quantity to the authenticated user's session cart."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    product = get_object_or_404(Product, slug=slug)
    if not request.user.is_authenticated:
        messages.info(request, "Connectez-vous pour ajouter un produit au panier.")
        login_url = reverse("login")
        next_url = reverse("product_detail", args=[product.slug])
        return redirect(f"{login_url}?next={next_url}")

    try:
        quantity = int(request.POST.get("quantity", "1"))
    except (TypeError, ValueError):
        messages.error(request, "Veuillez choisir une quantité valide.")
        return redirect("product_detail", slug=product.slug)

    if quantity < 1:
        messages.error(request, "La quantité doit être supérieure à zéro.")
        return redirect("product_detail", slug=product.slug)

    cart_items = request.session.get("cart", {})
    if not isinstance(cart_items, dict):
        cart_items = {}

    current_quantity = cart_items.get(product.slug, 0)
    if type(current_quantity) is not int or current_quantity < 0:
        current_quantity = 0
    if current_quantity + quantity > product.stock_quantity:
        messages.error(
            request,
            f"Il ne reste que {product.stock_quantity} exemplaire(s) en stock.",
        )
        return redirect("product_detail", slug=product.slug)
    cart_items[product.slug] = current_quantity + quantity
    request.session["cart"] = cart_items

    messages.success(request, f"{product.name} ajouté au panier.")
    return redirect("product_detail", slug=product.slug)


def remove_from_cart(request, slug):
    """Remove one product from the session cart; checkout remains separate."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cart_items = request.session.get("cart", {})
    if isinstance(cart_items, dict) and slug in cart_items:
        del cart_items[slug]
        request.session["cart"] = cart_items

    return redirect("cart")


def _create_order(request, cart_items, contact):
    """Create order/item snapshots and reserve stock atomically.

    Prices and names are copied onto OrderItem so later catalog edits do not
    rewrite purchase history. Product row locks protect the final stock check.
    Update the shipping constants in models.py to change the shared price rule.
    """
    if not cart_items:
        return None, "Votre panier est vide."

    order = None
    with transaction.atomic():
        products = {
            product.slug: product
            for product in Product.objects.select_for_update().filter(
                slug__in=cart_items.keys()
            )
        }
        if len(products) != len(cart_items):
            return None, "Un ou plusieurs produits du panier ne sont plus disponibles."

        subtotal = Decimal("0.00")
        order_lines = []
        for slug, quantity in cart_items.items():
            product = products[slug]
            if type(quantity) is not int or quantity < 1:
                return None, "Le panier contient une quantité invalide."
            if quantity > product.stock_quantity:
                return None, (
                    f"Stock insuffisant pour {product.name} : "
                    f"{product.stock_quantity} disponible(s)."
                )

            line_total = product.price * quantity
            subtotal += line_total
            order_lines.append((product, quantity))

        transport_fee = (
            STANDARD_SHIPPING_FEE
            if subtotal < FREE_SHIPPING_THRESHOLD
            else Decimal("0.00")
        )
        order = Order.objects.create(
            user=request.user,
            first_name=contact.cleaned_data["first_name"],
            last_name=contact.cleaned_data["last_name"],
            email=contact.cleaned_data["email"],
            phone=contact.cleaned_data["phone"],
            subtotal=subtotal,
            transport_fee=transport_fee,
            total=subtotal + transport_fee,
            status=Order.Status.AWAITING_PAYMENT,
            inventory_reserved=True,
        )
        OrderItem.objects.bulk_create([
            OrderItem(
                order=order,
                product=product,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
            )
            for product, quantity in order_lines
        ])
        for product, quantity in order_lines:
            Product.objects.filter(
                pk=product.pk,
                stock_quantity__gte=quantity,
            ).update(stock_quantity=F("stock_quantity") - quantity)

    return order, None


def cart(request):
    """Maintain the session cart and submit a validated order for Stripe checkout."""
    cart_items = request.session.get("cart", {})
    if not isinstance(cart_items, dict):
        cart_items = {}

    checkout_error = None
    checkout_form = CheckoutContactForm(initial={
        "first_name": request.user.first_name if request.user.is_authenticated else "",
        "last_name": request.user.last_name if request.user.is_authenticated else "",
        "email": request.user.email if request.user.is_authenticated else "",
    })
    products = Product.objects.prefetch_related("tags").in_bulk(
        cart_items.keys(),
        field_name="slug",
    )
    if request.method == "POST":
        remove_slug = request.POST.get("remove_item")
        update_item = request.POST.get("update_item")

        if remove_slug in cart_items:
            del cart_items[remove_slug]
        elif update_item:
            slug, separator, action = update_item.partition(":")
            if separator and slug in cart_items and slug in products:
                quantity = cart_items[slug]
                if type(quantity) is not int or quantity < 1:
                    quantity = 1

                if action == "decrease":
                    quantity -= 1
                elif action == "increase":
                    if quantity < products[slug].stock_quantity:
                        quantity += 1
                    else:
                        checkout_error = (
                            f"Stock maximal atteint pour {products[slug].name}."
                        )

                if quantity < 1:
                    del cart_items[slug]
                else:
                    cart_items[slug] = quantity
        elif request.POST.get("submit_order") == "1":
            if not request.user.is_authenticated:
                messages.info(request, "Connectez-vous pour confirmer votre commande.")
                return redirect(f"{reverse('login')}?next={reverse('cart')}")

            checkout_form = CheckoutContactForm(request.POST)
            if checkout_form.is_valid():
                if not settings.STRIPE_SECRET_KEY:
                    checkout_error = "Le paiement en ligne n’est pas encore configuré."
                else:
                    order, checkout_error = _create_order(
                        request,
                        cart_items,
                        checkout_form,
                    )
                    if order is not None:
                        request.session["cart"] = {}
                        return _start_stripe_checkout(request, order)
            else:
                checkout_error = "Veuillez vérifier les coordonnées saisies."

        request.session["cart"] = cart_items

    rows = []
    valid_cart = {}
    total = 0

    for slug, quantity in cart_items.items():
        product = products.get(slug)
        if product is None or type(quantity) is not int or quantity < 1:
            continue

        line_total = product.price * quantity
        rows.append({
            "product": product,
            "quantity": quantity,
            "line_total": line_total,
            "stock_quantity": product.stock_quantity,
        })
        valid_cart[slug] = quantity
        total += line_total

    if valid_cart != cart_items:
        request.session["cart"] = valid_cart

    transport_fee = (
        STANDARD_SHIPPING_FEE
        if total > 0 and total < FREE_SHIPPING_THRESHOLD
        else Decimal("0.00")
    )
    return render(request, "cart.html", {
        "cart_items": rows,
        "cart_rows": rows,
        "cart_total": total,
        "subtotal": total,
        "transport_fee": transport_fee,
        "total": total + transport_fee,
        "free_shipping_threshold": FREE_SHIPPING_THRESHOLD,
        "checkout_error": checkout_error,
        "checkout_form": checkout_form,
    })


def login_page(request):
    """Show social login choices and whether Apple sign-in is configured."""
    next_url = _safe_login_redirect(request, request.GET.get("next"))
    if request.user.is_authenticated:
        return redirect(next_url)

    return render(request, "login.html", {
        "next": next_url,
        "apple_login_enabled": settings.APPLE_LOGIN_ENABLED,
    })


@login_required(login_url="login")
def profile(request):
    """Show the signed-in user's orders and their latest payment attempts."""
    return render(request, "profile.html", {
        "orders": request.user.orders.prefetch_related(
            "items__product",
            Prefetch(
                "payment_attempts",
                queryset=PaymentAttempt.objects.order_by("-created_at"),
                to_attr="ordered_payment_attempts",
            ),
        ),
        "profile": None,
    })


@login_required(login_url="login")
@require_POST
def create_checkout_session(request, order_id):
    """Start or resume checkout only for an order owned by the signed-in user."""
    order = get_object_or_404(
        Order,
        pk=order_id,
        user=request.user,
    )
    return _start_stripe_checkout(request, order)


def _start_stripe_checkout(request, order):
    """Create or reuse Stripe's hosted Checkout Session for a reserved order.

    Stripe Checkout can present Apple Pay and Google Pay when those payment
    methods are enabled in Stripe and available for the customer's device,
    browser, country, currency, and verified domain. They are not separate
    Django payment flows; configure wallet availability in Stripe Dashboard.
    Keep order/attempt IDs in both metadata objects so webhook events can be
    correlated even if the webhook arrives before the session ID is saved.
    """
    if not settings.STRIPE_SECRET_KEY:
        messages.error(request, "Le paiement en ligne n’est pas encore configuré.")
        return redirect("profile")

    try:
        import stripe
    except ImportError:
        logger.exception("Stripe SDK is not installed.")
        messages.error(request, "Le paiement en ligne est momentanément indisponible.")
        return redirect("profile")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    order = get_object_or_404(
        Order.objects.prefetch_related("items"),
        pk=order.pk,
        user=request.user,
    )
    if (
        order.status != Order.Status.AWAITING_PAYMENT
        or order.payment_status
        not in (
            Order.PaymentStatus.UNPAID,
            Order.PaymentStatus.PENDING,
            Order.PaymentStatus.FAILED,
        )
    ):
        messages.error(request, "Cette commande n’est pas disponible au paiement.")
        return redirect("profile")

    attempt = None
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            if (
                order.status != Order.Status.AWAITING_PAYMENT
                or order.payment_status
                not in (
                    Order.PaymentStatus.UNPAID,
                    Order.PaymentStatus.PENDING,
                    Order.PaymentStatus.FAILED,
                )
            ):
                messages.error(request, "Cette commande n’est pas disponible au paiement.")
                return redirect("profile")

            attempt = (
                order.payment_attempts.filter(status=PaymentAttempt.Status.PENDING)
                .order_by("-created_at")
                .first()
            )
            if attempt is None:
                if not order.inventory_reserved:
                    items = list(
                        order.items.select_related("product").order_by("product_id")
                    )
                    if not items or any(item.product_id is None for item in items):
                        messages.error(
                            request,
                            "Un produit de cette commande n’est plus disponible.",
                        )
                        return redirect("profile")

                    products = {
                        product.pk: product
                        for product in Product.objects.select_for_update().filter(
                            pk__in=[item.product_id for item in items]
                        )
                    }
                    if any(
                        item.product_id not in products
                        or products[item.product_id].stock_quantity < item.quantity
                        for item in items
                    ):
                        messages.error(
                            request,
                            "Le stock ne permet plus de réserver cette commande.",
                        )
                        return redirect("profile")

                    for item in items:
                        reserved = Product.objects.filter(
                            pk=item.product_id,
                            stock_quantity__gte=item.quantity,
                        ).update(
                            stock_quantity=F("stock_quantity") - item.quantity
                        )
                        if reserved != 1:
                            raise StockUnavailable
                    order.inventory_reserved = True
                    order.save(update_fields=("inventory_reserved", "updated_at"))

                attempt = PaymentAttempt.objects.create(
                    order=order,
                    amount=order.total,
                    currency=order.currency.lower(),
                )
            order.payment_status = Order.PaymentStatus.PENDING
            order.save(update_fields=("payment_status", "updated_at"))
    except StockUnavailable:
        messages.error(
            request,
            "Le stock a changé avant la réservation; le paiement n’a pas démarré.",
        )
        return redirect("profile")

    if attempt.checkout_session_id:
        try:
            existing_session = stripe.checkout.Session.retrieve(
                attempt.checkout_session_id
            )
        except stripe.StripeError:
            logger.exception(
                "Could not retrieve Stripe Checkout Session for order %s.",
                order.pk,
            )
            messages.error(
                request,
                "La session de paiement est momentanément indisponible.",
            )
            return redirect("profile")

        if existing_session.status == "open" and existing_session.url:
            return redirect(existing_session.url)
        if existing_session.status == "complete":
            messages.info(
                request,
                "Le paiement est en cours de confirmation.",
            )
            return redirect("profile")

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.status == PaymentAttempt.Status.PENDING:
                attempt.status = PaymentAttempt.Status.CANCELLED
                attempt.save(update_fields=("status", "updated_at"))
            order.payment_status = Order.PaymentStatus.FAILED
            order.save(update_fields=("payment_status", "updated_at"))
            attempt = PaymentAttempt.objects.create(
                order=order,
                amount=order.total,
                currency=order.currency.lower(),
            )
            order.payment_status = Order.PaymentStatus.PENDING
            order.save(update_fields=("payment_status", "updated_at"))

    # Use the order's stored price snapshots, never client-submitted cart prices.
    line_items = [
        {
            "price_data": {
                "currency": order.currency.lower(),
                "product_data": {"name": item.product_name},
                "unit_amount": int(item.unit_price * 100),
            },
            "quantity": item.quantity,
        }
        for item in order.items.all()
    ]
    if order.transport_fee:
        line_items.append({
            "price_data": {
                "currency": order.currency.lower(),
                "product_data": {"name": "Frais de livraison"},
                "unit_amount": int(order.transport_fee * 100),
            },
            "quantity": 1,
        })

    # This return page is user feedback only; the webhook remains authoritative.
    success_url = (
        f"{request.build_absolute_uri(reverse('payment_return'))}"
        f"?session_id={{CHECKOUT_SESSION_ID}}"
    )
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            customer_email=order.email,
            shipping_address_collection={"allowed_countries": ["FR"]},
            client_reference_id=str(order.pk),
            metadata={
                "order_id": str(order.pk),
                "payment_attempt_id": str(attempt.pk),
            },
            payment_intent_data={
                "metadata": {
                    "order_id": str(order.pk),
                    "payment_attempt_id": str(attempt.pk),
                },
            },
            success_url=success_url,
            cancel_url=request.build_absolute_uri(reverse("profile")),
            idempotency_key=f"order-{order.pk}-attempt-{attempt.pk}",
        )
    except stripe.StripeError:
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            order = Order.objects.select_for_update().get(pk=order.pk)
            attempt.status = PaymentAttempt.Status.FAILED
            attempt.save(update_fields=("status", "updated_at"))
            order.payment_status = Order.PaymentStatus.FAILED
            order.save(update_fields=("payment_status", "updated_at"))
        logger.exception(
            "Could not create Stripe Checkout Session for order %s.",
            order.pk,
        )
        messages.error(
            request,
            "Nu am putut porni plata. Încearcă din nou sau contactează atelierul.",
        )
        return redirect("profile")

    attempt.checkout_session_id = checkout_session.id
    attempt.save(update_fields=("checkout_session_id", "updated_at"))
    return redirect(checkout_session.url)


@login_required(login_url="login")
def payment_return(request):
    """Display the current server-side result without trusting the return URL."""
    session_id = request.GET.get("session_id")
    attempt = (
        PaymentAttempt.objects.select_related("order")
        .filter(checkout_session_id=session_id, order__user=request.user)
        .first()
        if session_id
        else None
    )
    if attempt and attempt.order.payment_status == Order.PaymentStatus.SUCCEEDED:
        message = "Votre paiement a été confirmé. Merci pour votre commande."
        payment_confirmed = True
    elif attempt:
        message = (
            "Votre paiement est en cours de confirmation. "
            "Le statut sera mis à jour dans votre compte."
        )
        payment_confirmed = False
    else:
        message = (
            "Nous n’avons pas pu vérifier cette session de paiement. "
            "Consultez le statut de votre commande dans votre compte."
        )
        payment_confirmed = False

    return render(request, "payment_return.html", {
        "message": message,
        "payment_confirmed": payment_confirmed,
    })


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Verify and process Stripe events once, under database locks.

    When adding event types, update the event sets below and explicitly map each
    event to its allowed order/payment transition. Never treat the browser return
    as proof of payment, and never release inventory until Stripe confirms that
    the Checkout Session expired or its asynchronous payment failed.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("Stripe webhook secret is not configured.")
        return HttpResponse("Webhook is not configured.", status=503)

    try:
        import stripe
    except ImportError:
        logger.exception("Stripe SDK is not installed.")
        return HttpResponse("Payment service is unavailable.", status=503)

    # Stripe's signature is checked against the raw body and endpoint secret.
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            request.body,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning("Rejected Stripe webhook with an invalid signature.")
        return HttpResponse("Invalid Stripe webhook.", status=400)

    event_id = event["id"]
    event_type = event["type"]
    stripe_object = event["data"]["object"]
    object_id = stripe_object.get("id")
    checkout_events = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    payment_intent_events = {
        "payment_intent.payment_failed",
        "payment_intent.succeeded",
    }

    # Persist the event ledger and order transition in one transaction so a
    # rollback leaves the event retryable and cannot partially update inventory.
    try:
        with transaction.atomic():
            webhook_event, _ = StripeWebhookEvent.objects.get_or_create(
                event_id=event_id,
                defaults={"event_type": event_type},
            )
            webhook_event = StripeWebhookEvent.objects.select_for_update().get(
                pk=webhook_event.pk
            )
            # Stripe retries deliveries; a processed event must have no second effect.
            if webhook_event.processed_at:
                return HttpResponse(status=200)

            if event_type in checkout_events | payment_intent_events and object_id:
                metadata = stripe_object.get("metadata") or {}
                attempt = None

                if event_type in checkout_events:
                    attempt = (
                        PaymentAttempt.objects.select_for_update()
                        .select_related("order")
                        .filter(checkout_session_id=object_id)
                        .first()
                    )

                # Metadata is the recovery path if the checkout-session ID has not
                # yet been persisted by the request that created the session.
                if attempt is None:
                    attempt_id = metadata.get("payment_attempt_id")
                    try:
                        attempt_id = int(attempt_id)
                    except (TypeError, ValueError):
                        attempt_id = None

                    if attempt_id is not None:
                        attempt = (
                            PaymentAttempt.objects.select_for_update()
                            .select_related("order")
                            .filter(pk=attempt_id)
                            .first()
                        )
                        if (
                            attempt is not None
                            and metadata.get("order_id") == str(attempt.order_id)
                            and (
                                (
                                    event_type in payment_intent_events
                                    and attempt.payment_intent_id in (None, object_id)
                                )
                                or attempt.checkout_session_id in (None, object_id)
                            )
                        ):
                            if event_type in checkout_events:
                                attempt.checkout_session_id = object_id
                        else:
                            attempt = None

                if attempt is not None:
                    order = Order.objects.select_for_update().get(pk=attempt.order_id)
                    webhook_event.order = order
                    if attempt.status != PaymentAttempt.Status.PENDING:
                        webhook_event.processed_at = timezone.now()
                        webhook_event.save(update_fields=("order", "processed_at"))
                        return HttpResponse(status=200)
                    amount = (
                        stripe_object.get("amount_total")
                        if event_type in checkout_events
                        else stripe_object.get("amount")
                    )
                    currency = stripe_object.get("currency", "").lower()
                    expected_amount = int(order.total * 100)
                    # Validate the amount and currency before applying any transition.
                    amount_matches = (
                        amount == expected_amount
                        and currency == order.currency.lower()
                    )
                    if not amount_matches:
                        logger.error(
                            "Stripe amount/currency mismatch for order %s, object %s.",
                            order.pk,
                            object_id,
                        )
                        attempt.status = PaymentAttempt.Status.FAILED
                        order.payment_status = Order.PaymentStatus.FAILED
                    elif event_type in payment_intent_events:
                        # A card decline is diagnostic; Checkout can still be retried,
                        # so the order remains pending and its reserved stock is held.
                        attempt.payment_intent_id = object_id
                        if event_type == "payment_intent.payment_failed":
                            error = stripe_object.get("last_payment_error") or {}
                            attempt.last_error_code = (
                                error.get("code")
                                if isinstance(error.get("code"), str)
                                else "payment_failed"
                            )[:100]
                        else:
                            attempt.last_error_code = ""
                    elif (
                        event_type in (
                            "checkout.session.completed",
                            "checkout.session.async_payment_succeeded",
                        )
                        and stripe_object.get("payment_status") == "paid"
                    ):
                        # Confirm fulfillment only for a paid Checkout Session.
                        attempt.status = PaymentAttempt.Status.SUCCEEDED
                        intent = stripe_object.get("payment_intent")
                        if isinstance(intent, str):
                            attempt.payment_intent_id = intent
                        attempt.last_error_code = ""
                        order.payment_status = Order.PaymentStatus.SUCCEEDED
                        order.status = Order.Status.PAID
                    elif event_type in (
                        "checkout.session.async_payment_failed",
                        "checkout.session.expired",
                    ):
                        # Stripe-confirmed expiry/failure ends this attempt and frees
                        # stock; a plain PaymentIntent decline does not do this.
                        attempt.status = (
                            PaymentAttempt.Status.CANCELLED
                            if event_type == "checkout.session.expired"
                            else PaymentAttempt.Status.FAILED
                        )
                        order.payment_status = Order.PaymentStatus.FAILED
                        if order.inventory_reserved:
                            items = list(order.items.all())
                            for item in items:
                                if item.product_id is not None:
                                    Product.objects.filter(pk=item.product_id).update(
                                        stock_quantity=F("stock_quantity") + item.quantity
                                    )
                            order.inventory_reserved = False

                    shipping_details = (
                        stripe_object.get("shipping_details")
                        or (
                            stripe_object.get("collected_information") or {}
                        ).get("shipping_details")
                    ) if event_type in checkout_events else None
                    updated_order_fields = [
                        "payment_status",
                        "status",
                        "inventory_reserved",
                        "updated_at",
                    ]
                    if shipping_details:
                        address = shipping_details.get("address") or {}
                        order.shipping_name = shipping_details.get("name") or ""
                        order.shipping_address_line1 = address.get("line1") or ""
                        order.shipping_address_line2 = address.get("line2") or ""
                        order.shipping_city = address.get("city") or ""
                        order.shipping_postal_code = address.get("postal_code") or ""
                        order.shipping_country = address.get("country") or ""
                        updated_order_fields.extend([
                            "shipping_name",
                            "shipping_address_line1",
                            "shipping_address_line2",
                            "shipping_city",
                            "shipping_postal_code",
                            "shipping_country",
                        ])

                    attempt.save(update_fields=(
                        "checkout_session_id",
                        "status",
                        "payment_intent_id",
                        "last_error_code",
                        "updated_at",
                    ))
                    order.save(update_fields=updated_order_fields)

            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=("order", "processed_at"))
    except DatabaseError:
        logger.exception("Failed to process Stripe webhook event %s.", event_id)
        raise
    return HttpResponse(status=200)


def google_login(request):
    """Begin Google's OAuth authorization-code flow and store one-time state.

    Change requested profile scopes here and in google_callback together. The
    redirect URI must exactly match the URI registered in Google Cloud Console.
    """
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
    )

    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",
    )

    # Bind the callback to this browser session and retain the PKCE verifier.
    request.session["google_oauth_state"] = state
    request.session["google_oauth_code_verifier"] = flow.code_verifier
    request.session["google_oauth_next"] = _safe_login_redirect(
        request,
        request.GET.get("next"),
    )

    return redirect(authorization_url)

def google_callback(request):
    """Exchange Google's authorization code and authenticate its verified user.

    Google accounts are matched by the email claim in Google's verified ID
    token; new local Django users are created on first sign-in. Do not accept
    identity/profile fields from the browser without verifying the token and
    its audience.
    """
    from google_auth_oauthlib.flow import Flow
    from google.oauth2 import id_token
    from google.auth.transport import requests

    state = request.session.get("google_oauth_state")
    code_verifier = request.session.get("google_oauth_code_verifier")

    if not state or not code_verifier:
        return redirect("/")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
        state=state,
    )

    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    flow.code_verifier = code_verifier

    flow.fetch_token(
        authorization_response=request.build_absolute_uri()
    )

    google_data = id_token.verify_oauth2_token(
        flow.credentials.id_token,
        requests.Request(),
        settings.GOOGLE_CLIENT_ID,
    )

    email = google_data.get("email")
    first_name = google_data.get("given_name", "")
    last_name = google_data.get("family_name", "")

    if not email:
        return redirect("/")

    # Reuse the local account for the email claim in Google's verified ID token.
    user = User.objects.filter(email=email).first()

    # Create a local account on the first successful Google sign-in.
    if user is None:
        user = User.objects.create_user(
            username=email,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )


    login(
        request,
        user,
        backend="django.contrib.auth.backends.ModelBackend",
    )

    next_url = _safe_login_redirect(
        request,
        request.session.pop("google_oauth_next", None),
    )
    request.session.pop("google_oauth_state", None)
    request.session.pop("google_oauth_code_verifier", None)

    return redirect(next_url)


def logout_view(request):
    """End the Django session and return to the storefront."""
    logout(request)
    return redirect("/")
