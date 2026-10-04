from django.db import models
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
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    description = models.TextField(blank=True)
    specifications = models.TextField(blank=True)
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
    moq = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        default=1.00,
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.company.name})"


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="products/images/", blank=True, null=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.product.name}"
