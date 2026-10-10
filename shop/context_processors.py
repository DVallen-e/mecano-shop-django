"""Template context helpers shared by pages rendered for the current request."""


def cart_summary(request):
    """Expose a safe item count for the site header without querying products."""
    cart = request.session.get("cart", {})
    item_count = 0

    if isinstance(cart, dict):
        item_count = sum(
            quantity
            for quantity in cart.values()
            if type(quantity) is int and quantity > 0
        )

    return {"cart_item_count": item_count}
