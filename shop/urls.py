from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("product=/<slug:slug>/", views.product_detail, name="product_detail"),
    path("cart/", views.cart, name="cart"),
    path("cart/add/<slug:slug>/", views.add_to_cart, name="add_to_cart"),
    path("cart/remove/<slug:slug>/", views.remove_from_cart, name="remove_from_cart"),
    path("login/", views.login_page, name="login"),
    path("profile/", views.profile, name="profile"),
    path("auth/google/", views.google_login, name="google_login"),
    path(
        "auth/google/callback/",
        views.google_callback,
        name="google_callback",
    ),
    path("auth/logout/", views.logout_view, name="logout"),
]