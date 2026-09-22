
# Create your models here.
from django.db import models


class Announcement(models.Model):
    text = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"

    def __str__(self):
        return self.text

class Tag(models.Model):
    text = models.CharField(max_length=50)

    def __str__(self):
        return self.text
    


class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    tags = models.ManyToManyField(Tag, blank=True)

    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    in_stock = models.BooleanField(default=True)

    image = models.ImageField(
        upload_to="products/",
        blank=True,
        null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name