from django.shortcuts import render, get_object_or_404
from .models import Announcement, Product, Tag
from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect
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
    product = get_object_or_404(Product, slug=slug)

    return render(
        request,
        "product_detail.html",
        {"product": product}
    )

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

    return redirect(authorization_url)

def google_callback(request):
    from google_auth_oauthlib.flow import Flow
    from google.oauth2 import id_token
    from google.auth.transport import requests

    state = request.session.get("google_oauth_state")

    if not state:
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

    # Delete state OAuth
    request.session.pop("google_oauth_state", None)