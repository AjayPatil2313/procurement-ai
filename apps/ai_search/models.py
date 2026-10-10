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
        indexes = [
            models.Index(fields=["company", "job_type", "-created_at"]),
            models.Index(fields=["status"]),
        ]

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
            models.Index(fields=["name"]),
            models.Index(fields=["company_role"]),
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
            models.Index(fields=["search_job", "result_type", "-match_score"]),
        ]

    @property
    def company_name(self):
        if self.external_company:
            return self.external_company.name
        return ""

    @property
    def location(self):
        if self.external_company:
            parts = [p for p in [self.external_company.city, self.external_company.country] if p]
            return ", ".join(parts) if parts else "Global"
        return "Global"

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


class MatchingParameter(models.Model):
    """
    Dynamic Company Matching Criteria & Score Parameters:
    Configured per company to define how candidate buyers/suppliers are evaluated
    during AI search (Find Buyers / Find Suppliers). Allows dynamic weights,
    mandatory rules, bonus boosts, and full CRUD.
    """
    class RuleType(models.TextChoices):
        WEIGHTED = "weighted", "Weighted (Percentage Contribution)"
        MANDATORY = "mandatory", "Mandatory (Must Satisfy)"
        BONUS = "bonus", "Bonus (Boost Points)"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="matching_parameters",
    )
    name = models.CharField(max_length=150)
    parameter_key = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    criteria_value = models.CharField(max_length=255, blank=True)
    rule_type = models.CharField(
        max_length=20,
        choices=RuleType.choices,
        default=RuleType.WEIGHTED,
    )
    weight_percentage = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "-weight_percentage", "id"]
        verbose_name = "Matching Parameter"
        verbose_name_plural = "Matching Parameters"

    def __str__(self):
        return f"{self.name} ({self.weight_percentage}%) - {self.company.name}"


DEFAULT_MATCHING_PARAMETERS = [
    {
        "name": "Legal Entity Structure (Pvt Ltd / Ltd)",
        "parameter_key": "entity_type",
        "criteria_value": "Private Limited (Pvt Ltd), Public Limited, Corporate Entity",
        "description": "Target buyer must be an officially incorporated Private Limited (Pvt Ltd) or Public Ltd commercial entity with corporate standing.",
        "rule_type": "mandatory",
        "weight_percentage": 10,
    },
    {
        "name": "Verified Contact & Registration",
        "parameter_key": "verified_status",
        "criteria_value": "Verified Phone, Direct Corporate Email & Official Web Domain",
        "description": "Company must possess reachable communication channels, confirmed phone lines, and verified operational presence.",
        "rule_type": "weighted",
        "weight_percentage": 10,
    },
    {
        "name": "Geographic Location & Delivery Proximity",
        "parameter_key": "location",
        "criteria_value": "Regional Hub, Industrial Corridor, Domestic Proximity",
        "description": "Proximity between buyer plant or project location and target logistics dispatch corridor.",
        "rule_type": "weighted",
        "weight_percentage": 10,
    },
    {
        "name": "Product Technical Matching",
        "parameter_key": "product_matching",
        "criteria_value": "Direct Technical Compatibility & SKU Relevance",
        "description": "Direct operational alignment between the offered product specifications and the buyer's procurement scope.",
        "rule_type": "weighted",
        "weight_percentage": 15,
    },
    {
        "name": "Product Category Alignment",
        "parameter_key": "categories_match",
        "criteria_value": "Target Industrial Category & Sector Classification",
        "description": "Buyer's business domain must actively utilize and procure within the seller's specific product category.",
        "rule_type": "weighted",
        "weight_percentage": 10,
    },
    {
        "name": "Buyer Requirement & Demand Intent",
        "parameter_key": "buyer_req_match",
        "criteria_value": "Active Tender, Expansion Capex, Recurring Maintenance Demand",
        "description": "Buyer demonstrates an active buying signal, vendor empanelement window, ongoing RFQ, or replenishment cycle.",
        "rule_type": "weighted",
        "weight_percentage": 15,
    },
    {
        "name": "Industry Vertical Match",
        "parameter_key": "industry_match",
        "criteria_value": "Manufacturing, Infrastructure, EPC, Chemical, Heavy Engineering",
        "description": "Buyer operates in an industrial vertical that routinely consumes raw materials, equipment, or components.",
        "rule_type": "weighted",
        "weight_percentage": 10,
    },
    {
        "name": "Company Specifications (Size, Grade, Capacity)",
        "parameter_key": "specifications",
        "criteria_value": "Enterprise Scale, Quality Grade (ISO/MTC), Plant Capacity",
        "description": "Buyer's operational scale, unit capacity, and quality grade standards match supplier production volume.",
        "rule_type": "weighted",
        "weight_percentage": 10,
    },
    {
        "name": "Company Profile & Operational Relevance",
        "parameter_key": "profile_relevance",
        "criteria_value": "Operational Plant, Facility Infrastructure, Historical Track Record",
        "description": "Buyer's plant infrastructure and published commercial activities align with our technical portfolio.",
        "rule_type": "weighted",
        "weight_percentage": 5,
    },
    {
        "name": "B2B Commercial Operating Model",
        "parameter_key": "b2b_model",
        "criteria_value": "Commercial B2B Wholesale / Institutional Bulk Consumer",
        "description": "Buyer must operate on a B2B model (wholesale, institutional project, OEM, or distributor) and not retail/consumer.",
        "rule_type": "weighted",
        "weight_percentage": 5,
    },
]


def ensure_default_parameters_for_company(company):
    """Initializes standard matching parameters for a company if none exist."""
    if not company:
        return []
    existing = list(MatchingParameter.objects.filter(company=company))
    if existing:
        return existing

    created_params = []
    for data in DEFAULT_MATCHING_PARAMETERS:
        p = MatchingParameter.objects.create(
            company=company,
            name=data["name"],
            parameter_key=data["parameter_key"],
            criteria_value=data["criteria_value"],
            description=data["description"],
            rule_type=data["rule_type"],
            weight_percentage=data["weight_percentage"],
            is_active=True,
        )
        created_params.append(p)
    return created_params
