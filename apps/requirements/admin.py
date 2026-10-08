from django.contrib import admin
from apps.requirements.models import Requirement


@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = ("item_name", "company", "category", "target_price", "currency", "status", "created_at")
    list_filter = ("status", "category", "currency")
    search_fields = ("item_name", "description", "company__name")
    ordering = ("-created_at",)
