from django.shortcuts import render
from .models import Announcement
def index(request):

    announcement = Announcement.objects.filter(active=True).first()
    return render(request, "index.html", {"announcement": announcement})