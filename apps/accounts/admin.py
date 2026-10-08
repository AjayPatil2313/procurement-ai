from django.contrib import admin
from apps.accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "is_superuser", "is_staff", "is_active", "is_email_verified", "created_at")
    list_filter = ("is_superuser", "is_staff", "is_active", "is_email_verified")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("-created_at",)
