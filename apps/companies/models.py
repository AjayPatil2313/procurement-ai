from django.conf import settings
from django.db import models


class Company(models.Model):

    class CompanyType(models.TextChoices):
        BUYER = "BUYER", "Buyer"
        SELLER = "SELLER", "Seller"
        BOTH = "BOTH", "Buyer & Seller"

    class CompanySize(models.TextChoices):
        SMALL_1_10 = "1-10", "1-10"
        SMALL_11_50 = "11-50", "11-50"
        MEDIUM_51_200 = "51-200", "51-200"
        LARGE_201_1000 = "201-1000", "201-1000"
        ENTERPRISE_1000_PLUS = "1000+", "1000+"

    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)

    company_type = models.CharField(
        max_length=20,
        choices=CompanyType.choices,
        default=CompanyType.BUYER,
    )

    registration_no = models.CharField(
        max_length=100,
        blank=True,
    )

    gst_vat_no = models.CharField(
        max_length=100,
        blank=True,
    )

    industry = models.CharField(
        max_length=255,
        blank=True,
    )

    company_size = models.CharField(
        max_length=20,
        choices=CompanySize.choices,
        blank=True,
    )

    website = models.URLField(blank=True)

    email = models.EmailField(blank=True)

    phone = models.CharField(
        max_length=30,
        blank=True,
    )

    about = models.TextField(blank=True)

    description = models.TextField(blank=True)

    logo = models.ImageField(
        upload_to="companies/logos/",
        blank=True,
        null=True,
    )

    address_line1 = models.CharField(
        max_length=255,
        blank=True,
    )

    address_line2 = models.CharField(
        max_length=255,
        blank=True,
    )

    # Keep existing address for backward compatibility
    address = models.TextField(blank=True)

    city = models.CharField(
        max_length=100,
        blank=True,
    )

    state = models.CharField(
        max_length=100,
        blank=True,
    )

    country = models.CharField(
        max_length=100,
        default="India",
    )

    postal_code = models.CharField(
        max_length=20,
        blank=True,
    )

    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )

    longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )

    preferred_currency = models.CharField(
        max_length=3,
        default="INR",
    )

    is_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_companies",
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
        related_name="members",
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_membership",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
    )

    is_active = models.BooleanField(default=True)

    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.company.name}"


class CompanyPermission(models.Model):

    class PermissionType(models.TextChoices):
        READ = "READ", "Read"
        EDIT = "EDIT", "Edit"
        DELETE = "DELETE", "Delete"
        UPDATE = "UPDATE", "Update"
        IMPORT = "IMPORT", "Import"
        EXPORT = "EXPORT", "Export"

    module = models.CharField(
        max_length=50
    )

    permission = models.CharField(
        max_length=20,
        choices=PermissionType.choices
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module", "permission"],
                name="unique_module_permission",
            )
        ]

    def __str__(self):
        return f"{self.module} - {self.permission}"


class MemberPermission(models.Model):

    member = models.ForeignKey(
        CompanyMember,
        on_delete=models.CASCADE,
        related_name="permission_assignments",
    )

    permission = models.ForeignKey(
        CompanyPermission,
        on_delete=models.CASCADE,
        related_name="member_assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["member", "permission"],
                name="unique_member_permission",
            )
        ]

    def __str__(self):
        return (
            f"{self.member.user.email} - "
            f"{self.permission.module} - "
            f"{self.permission.permission}"
        )