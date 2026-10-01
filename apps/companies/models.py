from django.conf import settings
from django.db import models


class Company(models.Model):

    class CompanyType(models.TextChoices):
        BUYER = "BUYER", "Buyer"
        SELLER = "SELLER", "Seller"
        BOTH = "BOTH", "Buyer & Seller"

    name = models.CharField(max_length=255)

    legal_name = models.CharField(
        max_length=255,
        blank=True
    )

    company_type = models.CharField(
        max_length=20,
        choices=CompanyType.choices,
        default=CompanyType.BUYER
    )

    industry = models.CharField(
        max_length=255,
        blank=True
    )

    description = models.TextField(
        blank=True
    )

    website = models.URLField(
        blank=True
    )

    email = models.EmailField(
        blank=True
    )

    phone = models.CharField(
        max_length=30,
        blank=True
    )

    address = models.TextField(
        blank=True
    )

    city = models.CharField(
        max_length=100,
        blank=True
    )

    state = models.CharField(
        max_length=100,
        blank=True
    )

    country = models.CharField(
        max_length=100,
        default="India"
    )

    postal_code = models.CharField(
        max_length=20,
        blank=True
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_companies"
    )

    def __str__(self):
        return self.name


class CompanyMember(models.Model):

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Company Admin"
        USER = "USER", "Company User"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="members"
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_memberships"
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER
    )

    is_active = models.BooleanField(default=True)

    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "user"],
                name="unique_company_user"
            )
        ]

    def __str__(self):
        return f"{self.user.email} - {self.company.name}"

