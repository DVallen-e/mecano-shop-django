def cart_summary(request):
    cart = request.session.get("cart", {})
    item_count = 0

    if isinstance(cart, dict):
        item_count = sum(
            quantity
            for quantity in cart.values()
            if type(quantity) is int and quantity > 0
        )

    return {"cart_item_count": item_count}
