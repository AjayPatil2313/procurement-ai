from django.contrib import admin
from apps.ai_search.models import SearchJob, ExternalCompany, APILog, MatchingParameter


@admin.register(SearchJob)
class SearchJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "company", "user", "status", "total_results", "api_cost", "created_at")
    list_filter = ("job_type", "status")
    search_fields = ("search_query", "company__name", "user__email")
    ordering = ("-created_at",)


@admin.register(ExternalCompany)
class ExternalCompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "company_role", "country", "email", "created_at")
    list_filter = ("company_role", "country")
    search_fields = ("name", "domain", "email", "industry", "city")
    ordering = ("-created_at",)


@admin.register(APILog)
class APILogAdmin(admin.ModelAdmin):
    list_display = ("provider", "endpoint", "status_code", "cost", "created_at")
    list_filter = ("provider", "status_code")
    search_fields = ("provider", "endpoint")
    ordering = ("-created_at",)


@admin.register(MatchingParameter)
class MatchingParameterAdmin(admin.ModelAdmin):
    list_display = ("company", "name", "parameter_key", "rule_type", "weight_percentage", "is_active")
    list_filter = ("rule_type", "is_active")
    search_fields = ("name", "parameter_key", "company__name")
