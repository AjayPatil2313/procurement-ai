from django.conf import settings
from django.db import models
from apps.companies.models import Company
from apps.requirements.models import Requirement
from apps.catalog.models import Product


class SearchJob(models.Model):
    class JobType(models.TextChoices):
        FIND_SUPPLIERS = "find_suppliers", "Find Suppliers"
        FIND_BUYERS = "find_buyers", "Find Buyers"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="search_jobs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="search_jobs",
    )
    job_type = models.CharField(
        max_length=20,
        choices=JobType.choices,
        default=JobType.FIND_SUPPLIERS,
    )
    requirement = models.ForeignKey(
        Requirement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="search_jobs",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="search_jobs",
    )
    search_query = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    progress_percent = models.PositiveSmallIntegerField(default=0)
    total_results = models.IntegerField(default=0)
    queries_used = models.JSONField(default=list, blank=True)
    tokens_used = models.IntegerField(default=0)
    api_cost = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0.0000,
    )
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.requirement.item_name if self.requirement else (self.product.name if self.product else self.search_query or "Job")
        return f"{self.get_job_type_display()} - {target} (#{self.id})"


class ExternalCompany(models.Model):
    class CompanyRole(models.TextChoices):
        MANUFACTURER = "manufacturer", "Manufacturer"
        SUPPLIER = "supplier", "Supplier"
        TRADER = "trader", "Trader"
        EXPORTER = "exporter", "Exporter"
        DISTRIBUTOR = "distributor", "Distributor"
        END_USER = "end_user", "End User"

    name = models.CharField(max_length=255)
    website = models.CharField(max_length=255, blank=True)
    domain = models.CharField(max_length=150, db_index=True, blank=True)
    email = models.CharField(max_length=254, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
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
    company_role = models.CharField(
        max_length=30,
        choices=CompanyRole.choices,
        default=CompanyRole.SUPPLIER,
    )
    industry = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    source_urls = models.JSONField(default=list, blank=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "External companies"
        indexes = [
            models.Index(fields=["domain"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.country or 'Global'})"


class SearchResult(models.Model):
    class ResultType(models.TextChoices):
        SUPPLIER = "supplier", "Supplier"
        LEAD = "lead", "Lead"

    search_job = models.ForeignKey(
        SearchJob,
        on_delete=models.CASCADE,
        related_name="results",
    )
    external_company = models.ForeignKey(
        ExternalCompany,
        on_delete=models.CASCADE,
        related_name="search_results",
    )
    result_type = models.CharField(
        max_length=20,
        choices=ResultType.choices,
        default=ResultType.SUPPLIER,
    )
    product_title = models.CharField(max_length=255)
    price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_currency = models.CharField(max_length=3, blank=True, default="USD")
    price_converted = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    price_unit = models.CharField(max_length=30, blank=True)
    moq = models.CharField(max_length=50, blank=True)
    distance_km = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_global_cheaper = models.BooleanField(default=False)
    savings_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    match_score = models.PositiveSmallIntegerField(default=0)
    match_reason = models.TextField(blank=True)
    need_signal = models.TextField(blank=True)
    source_url = models.CharField(max_length=500, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-match_score", "-created_at"]
        indexes = [
            models.Index(fields=["search_job", "match_score"]),
        ]

    def __str__(self):
        return f"{self.external_company.name} - {self.product_title} ({self.match_score}%)"


class APILog(models.Model):
    provider = models.CharField(max_length=100)
    endpoint = models.CharField(max_length=255)
    status_code = models.IntegerField(default=200)
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0.0000,
    )
    search_job = models.ForeignKey(
        SearchJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="api_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider} - {self.endpoint} ({self.status_code})"
