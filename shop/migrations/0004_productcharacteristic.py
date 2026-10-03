from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0003_tag_product_tags"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductCharacteristic",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("value", models.TextField()),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="characteristics",
                        to="shop.product",
                    ),
                ),
            ],
            options={
                "verbose_name": "Product characteristic",
                "verbose_name_plural": "Product characteristics",
                "ordering": ("id",),
            },
        ),
    ]
