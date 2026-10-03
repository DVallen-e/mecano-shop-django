from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import render, get_object_or_404, redirect
from .models import Announcement, Product, Tag
from django.contrib.auth import get_user_model, login, logout
from django.conf import settings

User = get_user_model()
def index(request):
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
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    product = get_object_or_404(Product, slug=slug)
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
    cart_items[product.slug] = current_quantity + quantity
    request.session["cart"] = cart_items

    messages.success(request, f"{product.name} ajouté au panier.")
    return redirect("product_detail", slug=product.slug)


def remove_from_cart(request, slug):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cart_items = request.session.get("cart", {})
    if isinstance(cart_items, dict) and slug in cart_items:
        del cart_items[slug]
        request.session["cart"] = cart_items

    return redirect("cart")


def cart(request):
    cart_items = request.session.get("cart", {})
    if not isinstance(cart_items, dict):
        cart_items = {}

    checkout_error = None
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
                    quantity += 1

                if quantity < 1:
                    del cart_items[slug]
                else:
                    cart_items[slug] = quantity
        elif request.POST.get("submit_order") == "1":
            checkout_error = "La validation de commande n'est pas disponible pour le moment."

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
        })
        valid_cart[slug] = quantity
        total += line_total

    if valid_cart != cart_items:
        request.session["cart"] = valid_cart

    return render(request, "cart.html", {
        "cart_items": rows,
        "cart_rows": rows,
        "cart_total": total,
        "subtotal": total,
        "transport_fee": 0,
        "total": total,
        "checkout_error": checkout_error,
    })


def google_login(request):
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

    request.session["google_oauth_state"] = state
    request.session["google_oauth_code_verifier"] = flow.code_verifier

    return redirect(authorization_url)

def google_callback(request):
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

    # Căutăm userul după email
    user = User.objects.filter(email=email).first()

    # Dacă nu există, îl creăm
    if user is None:
        user = User.objects.create_user(
            username=email,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )


    login(request, user)

    request.session.pop("google_oauth_state", None)
    request.session.pop("google_oauth_code_verifier", None)

    return redirect("/")


def logout_view(request):
    logout(request)
    return redirect("/")
