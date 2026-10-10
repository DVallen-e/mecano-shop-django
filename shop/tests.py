"""Regression tests for catalog, cart, order, Stripe, and admin behavior.

Stripe API calls are mocked so the suite verifies our state transitions without
creating real payments. Add checkout/webhook lifecycle cases to StripePaymentTests.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductCharacteristic,
    Tag,
)


class ProductDetailTests(TestCase):
    """Product detail and catalog availability behavior."""

    def setUp(self):
        self.product = Product.objects.create(
            name="Huile boîte de vitesses",
            slug="huile-boite",
            description="Lubrifiant haute performance.",
            price="28.50",
            stock_quantity=5,
        )

    def test_product_detail_displays_product_information_and_actions(self):
        self.client.force_login(
            get_user_model().objects.create_user(
                username="buyer",
                email="buyer@example.com",
            )
        )
        response = self.client.get(
            reverse("product_detail", args=[self.product.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.name)
        self.assertContains(response, self.product.description)
        self.assertContains(response, "28,50")
        self.assertContains(response, 'data-product-price="28.50"')
        self.assertContains(response, "js/product_detail.js")
        self.assertNotContains(response, 'placeholder="Rechercher un produit..."')
        self.assertContains(response, 'class="site-header-cart relative')
        self.assertContains(response, 'class="site-header-logo shrink-0"')
        self.assertContains(response, 'class="bg-surface text-white product-detail-page"')
        self.assertContains(response, "Ajouter au panier")
        self.assertContains(response, 'name="quantity"')
        self.assertContains(response, "5 en stock")

    def test_anonymous_product_detail_prompts_for_login_instead_of_add_to_cart(self):
        response = self.client.get(
            reverse("product_detail", args=[self.product.slug])
        )

        self.assertContains(response, "Connectez-vous pour ajouter au panier")
        self.assertContains(response, reverse("login"))
        self.assertNotContains(response, 'data-product-purchase')

    def test_product_detail_displays_admin_managed_characteristics(self):
        ProductCharacteristic.objects.create(
            product=self.product,
            name="Couleur",
            value="Noir",
        )
        ProductCharacteristic.objects.create(
            product=self.product,
            name="Compatibilité",
            value="Boîtes manuelles",
        )

        response = self.client.get(
            reverse("product_detail", args=[self.product.slug])
        )

        self.assertContains(response, "Couleur")
        self.assertContains(response, "Noir")
        self.assertContains(response, "Compatibilité")
        self.assertContains(response, "Boîtes manuelles")

    def test_product_admin_includes_characteristic_inline(self):
        admin_user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin:shop_product_change", args=[self.product.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="characteristics-0-name"')
        self.assertContains(response, 'name="characteristics-0-value"')

    def test_out_of_stock_product_cannot_be_added_from_detail_page(self):
        self.product.stock_quantity = 0
        self.product.save()

        response = self.client.get(
            reverse("product_detail", args=[self.product.slug])
        )

        self.assertContains(response, "Produit actuellement indisponible")
        self.assertNotContains(response, "Ajouter au panier")

    def test_out_of_stock_product_is_disabled_in_catalog(self):
        self.product.stock_quantity = 0
        self.product.save()

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "aria-disabled=\"true\"", count=2)
        self.assertContains(response, "Produit indisponible — hors stock")
        self.assertNotContains(
            response,
            f'href="{reverse("product_detail", args=[self.product.slug])}"',
        )


class CartTests(TestCase):
    """Session cart, checkout validation, and order creation behavior."""

    def setUp(self):
        self.product = Product.objects.create(
            name="Huile moteur",
            slug="huile-moteur",
            price="12.50",
            stock_quantity=10,
        )
        self.user = get_user_model().objects.create_user(
            username="buyer",
            email="buyer@example.com",
        )
        self.client.force_login(self.user)

    def test_anonymous_user_cannot_add_product_to_cart(self):
        self.client.logout()

        response = self.client.post(
            reverse("add_to_cart", args=[self.product.slug]),
            {"quantity": "2"},
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('product_detail', args=[self.product.slug])}",
        )
        self.assertNotIn("cart", self.client.session)

    def test_login_page_offers_google_and_apple_options(self):
        self.client.logout()
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continuer avec Google")
        self.assertContains(response, "Continuer avec Apple")
        self.assertContains(response, 'disabled')
        self.assertContains(response, reverse("google_login"))

    @override_settings(APPLE_LOGIN_ENABLED=True)
    def test_configured_apple_button_uses_allauth_apple_login_route(self):
        self.client.logout()
        response = self.client.get(reverse("login"))

        self.assertContains(response, reverse("apple_login"))
        self.assertNotContains(response, "disponible après la configuration")

    def test_profile_requires_authentication_and_uses_profile_design(self):
        self.client.logout()
        response = self.client.get(reverse("profile"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('profile')}",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mon compte")
        self.assertContains(response, "Aucune commande")

    def test_add_to_cart_updates_header_count_and_cart_total(self):
        response = self.client.post(
            reverse("add_to_cart", args=[self.product.slug]),
            {"quantity": "2"},
        )

        self.assertRedirects(
            response,
            reverse("product_detail", args=[self.product.slug]),
        )
        self.assertEqual(
            self.client.session["cart"],
            {self.product.slug: 2},
        )

        response = self.client.get(reverse("cart"))
        self.assertContains(response, "Votre panier")
        self.assertContains(response, self.product.name)
        self.assertContains(response, reverse("product_detail", args=[self.product.slug]))
        self.assertEqual(response.context["cart_item_count"], 2)
        self.assertEqual(response.context["cart_total"], Decimal("25.00"))
        self.assertEqual(response.context["subtotal"], Decimal("25.00"))
        self.assertEqual(response.context["cart_items"][0]["quantity"], 2)

    def test_cart_displays_product_tags_and_handles_missing_images(self):
        tag = Tag.objects.create(text="Moteur")
        self.product.tags.add(tag)
        session = self.client.session
        session["cart"] = {self.product.slug: 1}
        session.save()

        response = self.client.get(reverse("cart"))

        self.assertContains(response, "Moteur")
        self.assertContains(response, "Photo indisponible")

    def test_cart_quantity_controls_update_session(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 2}
        session.save()

        self.client.post(
            reverse("cart"),
            {"update_item": f"{self.product.slug}:increase"},
        )

        self.assertEqual(self.client.session["cart"], {self.product.slug: 3})

    @override_settings(STRIPE_SECRET_KEY="sk_test_placeholder")
    @patch("stripe.checkout.Session.create")
    def test_checkout_creates_order_reserves_stock_and_starts_stripe(
        self,
        create_session,
    ):
        create_session.return_value.id = "cs_test_direct_order"
        create_session.return_value.url = "https://checkout.stripe.test/direct"
        session = self.client.session
        session["cart"] = {self.product.slug: 2}
        session.save()

        response = self.client.post(
            reverse("cart"),
            {
                "submit_order": "1",
                "first_name": "Jean",
                "last_name": "Dupont",
                "email": "jean@example.com",
                "phone": "0601020304",
            },
        )

        order = Order.objects.get(user=self.user)
        item = OrderItem.objects.get(order=order)
        self.assertRedirects(
            response,
            "https://checkout.stripe.test/direct",
            fetch_redirect_response=False,
        )
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertTrue(order.inventory_reserved)
        self.assertEqual(order.subtotal, Decimal("25.00"))
        self.assertEqual(order.transport_fee, Decimal("7.00"))
        self.assertEqual(order.total, Decimal("32.00"))
        self.assertEqual(item.product_name, self.product.name)
        self.assertEqual(item.unit_price, Decimal("12.50"))
        self.assertEqual(item.quantity, 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(self.client.session["cart"], {})
        self.assertEqual(order.payment_attempts.get().checkout_session_id, "cs_test_direct_order")
        self.assertEqual(
            create_session.call_args.kwargs["idempotency_key"],
            f"order-{order.pk}-attempt-{order.payment_attempts.get().pk}",
        )

    @override_settings(STRIPE_SECRET_KEY="")
    def test_checkout_does_not_create_order_without_stripe_configuration(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 1}
        session.save()

        response = self.client.post(
            reverse("cart"),
            {
                "submit_order": "1",
                "first_name": "Jean",
                "last_name": "Dupont",
                "email": "jean@example.com",
                "phone": "0601020304",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.client.session["cart"], {self.product.slug: 1})

    def test_checkout_requires_valid_contact_details(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 1}
        session.save()

        response = self.client.post(
            reverse("cart"),
            {
                "submit_order": "1",
                "first_name": "Jean",
                "last_name": "Dupont",
                "email": "not-an-email",
                "phone": "0601020304",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Veuillez vérifier les coordonnées saisies.")
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.client.session["cart"], {self.product.slug: 1})

    @override_settings(STRIPE_SECRET_KEY="sk_test_placeholder")
    def test_checkout_revalidates_stock_before_creating_order(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 2}
        session.save()
        self.product.stock_quantity = 1
        self.product.save()

        response = self.client.post(
            reverse("cart"),
            {
                "submit_order": "1",
                "first_name": "Jean",
                "last_name": "Dupont",
                "email": "jean@example.com",
                "phone": "0601020304",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuffisant")
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.client.session["cart"], {self.product.slug: 2})

    def test_workshop_admin_approval_reserves_stock_and_cancellation_releases_it(self):
        order = Order.objects.create(
            user=self.user,
            first_name="Jean",
            last_name="Dupont",
            email="jean@example.com",
            phone="0601020304",
            status=Order.Status.AWAITING_REVIEW,
            subtotal=Decimal("25.00"),
            transport_fee=Decimal("7.00"),
            total=Decimal("32.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )
        admin_user = get_user_model().objects.create_superuser(
            username="workshop-admin",
            email="admin@example.com",
            password="test-password-for-admin",
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("admin:shop_order_changelist"),
            {"action": "approve_for_payment", "_selected_action": [str(order.pk)]},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.product.stock_quantity, 8)

        self.client.post(
            reverse("admin:shop_order_changelist"),
            {"action": "cancel_orders", "_selected_action": [str(order.pk)]},
        )
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock_quantity, 10)

    def test_add_to_cart_rejects_non_positive_quantity(self):
        response = self.client.post(
            reverse("add_to_cart", args=[self.product.slug]),
            {"quantity": "0"},
        )

        self.assertRedirects(
            response,
            reverse("product_detail", args=[self.product.slug]),
        )
        self.assertNotIn("cart", self.client.session)

    def test_add_to_cart_rejects_quantity_above_stock(self):
        response = self.client.post(
            reverse("add_to_cart", args=[self.product.slug]),
            {"quantity": "11"},
        )

        self.assertRedirects(
            response,
            reverse("product_detail", args=[self.product.slug]),
        )
        self.assertNotIn("cart", self.client.session)

    def test_cart_shipping_is_free_at_120_euros_and_costs_seven_below(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 1}
        session.save()

        response = self.client.get(reverse("cart"))
        self.assertEqual(response.context["transport_fee"], Decimal("7.00"))
        self.assertEqual(response.context["total"], Decimal("19.50"))

        self.product.price = Decimal("120.00")
        self.product.save()
        response = self.client.get(reverse("cart"))
        self.assertEqual(response.context["transport_fee"], Decimal("0.00"))
        self.assertEqual(response.context["total"], Decimal("120.00"))

    def test_cart_item_can_be_removed(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 2}
        session.save()

        response = self.client.post(
            reverse("remove_from_cart", args=[self.product.slug]),
        )

        self.assertRedirects(response, reverse("cart"))
        self.assertEqual(self.client.session["cart"], {})


class StripePaymentTests(TestCase):
    """Stripe Checkout, webhook idempotency, and stale-session admin behavior."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="stripe-buyer",
            email="buyer@example.com",
        )
        self.product = Product.objects.create(
            name="Filtre à huile",
            slug="filtre-huile-stripe",
            price=Decimal("25.00"),
            stock_quantity=4,
        )
        self.order = Order.objects.create(
            user=self.user,
            first_name="Jean",
            last_name="Dupont",
            email="buyer@example.com",
            phone="0601020304",
            status=Order.Status.AWAITING_PAYMENT,
            subtotal=Decimal("25.00"),
            transport_fee=Decimal("7.00"),
            total=Decimal("32.00"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=1,
        )
        self.client.force_login(self.user)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_checkout_does_not_start_when_stripe_is_not_configured(self):
        response = self.client.post(
            reverse("create_checkout_session", args=[self.order.pk])
        )

        self.assertRedirects(response, reverse("profile"))
        self.assertFalse(self.order.payment_attempts.exists())

    @override_settings(STRIPE_SECRET_KEY="sk_test_placeholder")
    @patch("stripe.checkout.Session.create")
    def test_approved_order_starts_stripe_checkout(self, create_session):
        create_session.return_value.id = "cs_test_order"
        create_session.return_value.url = "https://checkout.stripe.test/session"

        response = self.client.post(
            reverse("create_checkout_session", args=[self.order.pk])
        )

        self.assertRedirects(
            response,
            "https://checkout.stripe.test/session",
            fetch_redirect_response=False,
        )
        attempt = self.order.payment_attempts.get()
        self.order.refresh_from_db()
        self.assertEqual(attempt.checkout_session_id, "cs_test_order")
        self.assertEqual(attempt.amount, Decimal("32.00"))
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(create_session.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"], 2500)
        self.assertEqual(create_session.call_args.kwargs["line_items"][1]["price_data"]["unit_amount"], 700)
        self.assertEqual(
            create_session.call_args.kwargs["shipping_address_collection"],
            {"allowed_countries": ["FR"]},
        )
        self.assertEqual(
            create_session.call_args.kwargs["payment_intent_data"]["metadata"],
            {
                "order_id": str(self.order.pk),
                "payment_attempt_id": str(attempt.pk),
            },
        )

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_placeholder")
    @patch("stripe.Webhook.construct_event")
    def test_payment_intent_failure_is_recorded_but_keeps_stock_reserved(
        self,
        construct_event,
    ):
        self.order.inventory_reserved = True
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("inventory_reserved", "payment_status"))
        self.product.stock_quantity = 3
        self.product.save(update_fields=("stock_quantity",))
        attempt = self.order.payment_attempts.create(
            status=PaymentAttempt.Status.PENDING,
            amount=self.order.total,
            currency="eur",
        )
        construct_event.return_value = {
            "id": "evt_test_card_declined",
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "id": "pi_test_declined",
                    "amount": 3200,
                    "currency": "eur",
                    "metadata": {
                        "order_id": str(self.order.pk),
                        "payment_attempt_id": str(attempt.pk),
                    },
                    "last_payment_error": {
                        "code": "card_declined",
                        "message": "Your card was declined.",
                    },
                },
            },
        }

        response = self.client.post(
            reverse("stripe_webhook"),
            data=b"stripe-event",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid-signature",
        )

        self.assertEqual(response.status_code, 200)
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(attempt.payment_intent_id, "pi_test_declined")
        self.assertEqual(attempt.last_error_code, "card_declined")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)
        self.assertTrue(self.order.inventory_reserved)
        self.assertEqual(self.product.stock_quantity, 3)
        profile_response = self.client.get(reverse("profile"))
        self.assertContains(profile_response, "Le paiement a été refusé.")
        self.assertContains(profile_response, "Vos articles restent réservés")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_placeholder")
    @patch("stripe.Webhook.construct_event")
    def test_signed_checkout_webhook_confirms_payment_once(self, construct_event):
        attempt = self.order.payment_attempts.create(
            status="pending",
            amount=self.order.total,
            currency="eur",
        )
        event = {
            "id": "evt_test_paid",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_paid",
                    "amount_total": 3200,
                    "currency": "eur",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_paid",
                    "metadata": {
                        "order_id": str(self.order.pk),
                        "payment_attempt_id": str(attempt.pk),
                    },
                    "shipping_details": {
                        "name": "Jean Dupont",
                        "address": {
                            "line1": "1 rue Exemple",
                            "line2": None,
                            "city": "Saint-Étienne",
                            "postal_code": "42000",
                            "country": "FR",
                        },
                    },
                },
            },
        }
        construct_event.return_value = event

        url = reverse("stripe_webhook")
        response = self.client.post(
            url,
            data=b"stripe-event",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid-signature",
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.SUCCEEDED)
        self.assertEqual(self.order.shipping_city, "Saint-Étienne")
        self.assertEqual(self.order.shipping_address_line2, "")
        self.assertEqual(attempt.status, "succeeded")
        self.assertEqual(attempt.payment_intent_id, "pi_test_paid")
        self.assertEqual(attempt.checkout_session_id, "cs_test_paid")

        response = self.client.post(
            url,
            data=b"stripe-event",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid-signature",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order.stripe_events.count(), 1)

    def test_payment_return_confirmation_is_in_french(self):
        self.order.status = Order.Status.PAID
        self.order.payment_status = Order.PaymentStatus.SUCCEEDED
        self.order.save(update_fields=("status", "payment_status"))
        self.order.payment_attempts.create(
            checkout_session_id="cs_test_french",
            status="succeeded",
            amount=self.order.total,
            currency="eur",
        )

        response = self.client.get(
            reverse("payment_return"),
            {"session_id": "cs_test_french"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Votre paiement a été confirmé. Merci pour votre commande.",
        )
        self.assertNotContains(response, "Plata a fost confirmată")

    @override_settings(STRIPE_SECRET_KEY="sk_test_placeholder")
    def test_admin_expires_stale_stripe_session_and_releases_reserved_stock(self):
        self.order.inventory_reserved = True
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("inventory_reserved", "payment_status"))
        self.product.stock_quantity = 3
        self.product.save(update_fields=("stock_quantity",))
        attempt = self.order.payment_attempts.create(
            checkout_session_id="cs_test_stale",
            status=PaymentAttempt.Status.PENDING,
            amount=self.order.total,
            currency="eur",
        )
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(hours=11)
        )
        admin_user = get_user_model().objects.create_superuser(
            username="stripe-admin",
            email="stripe-admin@example.com",
            password="test-password-for-admin",
        )
        self.client.force_login(admin_user)

        with (
            patch("stripe.checkout.Session.retrieve") as retrieve,
            patch("stripe.checkout.Session.expire") as expire,
        ):
            retrieve.return_value.status = "open"
            expire.return_value.status = "expired"
            expire.return_value.id = "cs_test_stale"
            response = self.client.post(
                reverse("admin:shop_order_changelist"),
                {
                    "action": "expire_stale_checkout_sessions",
                    "_selected_action": [str(self.order.pk)],
                },
            )

        self.assertEqual(response.status_code, 302)
        retrieve.assert_called_once_with("cs_test_stale")
        expire.assert_called_once_with("cs_test_stale")
        self.order.refresh_from_db()
        attempt.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.CANCELLED)
        self.assertEqual(attempt.last_error_code, "session_expired")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.FAILED)
        self.assertFalse(self.order.inventory_reserved)
        self.assertEqual(self.product.stock_quantity, 4)
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    @override_settings(STRIPE_SECRET_KEY="sk_test_placeholder")
    def test_admin_does_not_release_stock_for_recent_or_completed_checkout(self):
        self.order.inventory_reserved = True
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("inventory_reserved", "payment_status"))
        self.product.stock_quantity = 3
        self.product.save(update_fields=("stock_quantity",))
        self.order.payment_attempts.create(
            checkout_session_id="cs_test_recent",
            status=PaymentAttempt.Status.PENDING,
            amount=self.order.total,
            currency="eur",
        )
        admin_user = get_user_model().objects.create_superuser(
            username="stripe-admin",
            email="stripe-admin@example.com",
            password="test-password-for-admin",
        )
        self.client.force_login(admin_user)

        with patch("stripe.checkout.Session.retrieve") as retrieve:
            response = self.client.post(
                reverse("admin:shop_order_changelist"),
                {
                    "action": "expire_stale_checkout_sessions",
                    "_selected_action": [str(self.order.pk)],
                },
            )

        self.assertEqual(response.status_code, 302)
        retrieve.assert_not_called()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)

        attempt = self.order.payment_attempts.get()
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(hours=11)
        )
        with patch("stripe.checkout.Session.retrieve") as retrieve:
            retrieve.return_value.status = "complete"
            response = self.client.post(
                reverse("admin:shop_order_changelist"),
                {
                    "action": "expire_stale_checkout_sessions",
                    "_selected_action": [str(self.order.pk)],
                },
            )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)
        self.assertTrue(self.order.inventory_reserved)
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)


class AdminCleanupTests(TestCase):
    """Admin visibility rules for Django and django-allauth models."""

    def test_allauth_account_models_are_hidden_but_auth_users_remain(self):
        admin_user = get_user_model().objects.create_superuser(
            username="clean-admin",
            email="admin@example.com",
            password="test-password-for-admin",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        app_labels = {
            app["app_label"] for app in response.context["available_apps"]
        }
        self.assertNotIn("account", app_labels)
        self.assertNotIn("socialaccount", app_labels)
        self.assertIn("auth", app_labels)
