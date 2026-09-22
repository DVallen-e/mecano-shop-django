from django.shortcuts import render, get_object_or_404
from .models import Announcement, Product, Tag

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