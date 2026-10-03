from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Announcement, Product, ProductCharacteristic, Tag


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("text", "active")
    list_filter = ("active",)

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("text",)


class ProductCharacteristicInline(admin.TabularInline):
    model = ProductCharacteristic
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "price",
        "in_stock",
    )

    prepopulated_fields = {
        "slug": ("name",),
    }

    inlines = (ProductCharacteristicInline,)