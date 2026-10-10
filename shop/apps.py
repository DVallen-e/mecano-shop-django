"""Application startup hooks and Django Admin presentation for the shop."""

from django.apps import AppConfig


class ShopConfig(AppConfig):
    """Configure the shop app and hide low-level django-allauth records in Admin."""

    name = 'shop'

    def ready(self):
        # Social account data is managed by the sign-in flow, not shop staff.
        from django.contrib import admin
        from allauth.account.models import EmailAddress, EmailConfirmation
        from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken

        for model in (
            EmailAddress,
            EmailConfirmation,
            SocialAccount,
            SocialApp,
            SocialToken,
        ):
            if admin.site.is_registered(model):
                admin.site.unregister(model)
