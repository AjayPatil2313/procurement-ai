from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.companies.models import Company


class Category(models.Model):
    name = models.CharField(max_length=150)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    hsn_code = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    class ItemType(models.TextChoices):
        PRODUCT = "product", "Product"
        SERVICE = "service", "Service"

    class SearchScope(models.TextChoices):
        NEARBY = "nearby", "Nearby"
        COUNTRY = "country", "Country"
        GLOBAL = "global", "Global"

    class Availability(models.TextChoices):
        IN_STOCK = "IN_STOCK", "In Stock"
        MADE_TO_ORDER = "MADE_TO_ORDER", "Made to Order"
        AVAILABLE_ON_REQUEST = "AVAILABLE_ON_REQUEST", "Available on Request"
        OUT_OF_STOCK = "OUT_OF_STOCK", "Out of Stock"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="products",
    )
    type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.PRODUCT,
    )
    name = models.CharField(max_length=255)
    sku = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Stock Keeping Unit or internal Catalog/Part Code",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    description = models.TextField(blank=True)
    specifications = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_min = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_max = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default="INR")
    unit = models.CharField(max_length=30, blank=True, default="pcs")
    minimum_order_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        default=1.00,
    )
    moq = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        default=1.00,
    )
    availability = models.CharField(
        max_length=50,
        choices=Availability.choices,
        default=Availability.IN_STOCK,
        blank=True,
    )
    location = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
    )
    target_industries = models.JSONField(default=list, blank=True)
    target_regions = models.JSONField(default=list, blank=True)
    search_scope = models.CharField(
        max_length=20,
        choices=SearchScope.choices,
        default=SearchScope.COUNTRY,
    )
    radius_km = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.company.name})"

    def save(self, *args, **kwargs):
        if self.price is not None and self.price_min is None:
            self.price_min = self.price
        elif self.price_min is not None and self.price is None:
            self.price = self.price_min

        if self.minimum_order_quantity is not None and self.moq is None:
            self.moq = self.minimum_order_quantity
        elif self.moq is not None and self.minimum_order_quantity is None:
            self.minimum_order_quantity = self.moq

        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="products/images/", blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.product.name}"
