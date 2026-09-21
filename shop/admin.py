from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("text", "active")
    list_filter = ("active",)