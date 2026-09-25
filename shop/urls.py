from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("product=/<slug:slug>/", views.product_detail, name="product_detail"),
    path("auth/google/", views.google_login, name="google_login"),
    path(
        "auth/google/callback/",
        views.google_callback,
        name="google_callback",
    ),
]