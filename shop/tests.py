from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Product


class ProductDetailTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Huile boîte de vitesses",
            slug="huile-boite",
            description="Lubrifiant haute performance.",
            price="28.50",
        )

    def test_product_detail_displays_product_information_and_actions(self):
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

    def test_out_of_stock_product_cannot_be_added_from_detail_page(self):
        self.product.in_stock = False
        self.product.save()

        response = self.client.get(
            reverse("product_detail", args=[self.product.slug])
        )

        self.assertContains(response, "Produit actuellement indisponible")
        self.assertNotContains(response, "Ajouter au panier")


class CartTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Huile moteur",
            slug="huile-moteur",
            price="12.50",
        )

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
        self.assertEqual(response.context["cart_item_count"], 2)
        self.assertEqual(response.context["cart_total"], Decimal("25.00"))

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

    def test_cart_item_can_be_removed(self):
        session = self.client.session
        session["cart"] = {self.product.slug: 2}
        session.save()

        response = self.client.post(
            reverse("remove_from_cart", args=[self.product.slug]),
        )

        self.assertRedirects(response, reverse("cart"))
        self.assertEqual(self.client.session["cart"], {})
