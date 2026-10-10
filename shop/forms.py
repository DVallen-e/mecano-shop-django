"""Validation for customer-provided checkout contact details."""

from django import forms


class CheckoutContactForm(forms.Form):
    """Contact data copied onto the order before the customer leaves for Stripe."""

    first_name = forms.CharField(max_length=150, strip=True)
    last_name = forms.CharField(max_length=150, strip=True)
    email = forms.EmailField(max_length=254)
    phone = forms.CharField(max_length=32, strip=True)
