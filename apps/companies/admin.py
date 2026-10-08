from django.contrib import admin
from apps.companies.models import (
    Company,
    CompanyMember,
    CompanyRole,
    CompanyPermission,
    MemberPermission,
    RolePermission,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "company_type", "industry", "country", "is_verified", "is_active", "created_at")
    list_filter = ("company_type", "is_verified", "is_active", "country")
    search_fields = ("name", "domain", "industry", "country", "city")
    ordering = ("-created_at",)


@admin.register(CompanyMember)
class CompanyMemberAdmin(admin.ModelAdmin):
    list_display = ("user", "company", "role", "custom_role", "is_active", "joined_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__email", "company__name")
    ordering = ("-joined_at",)


@admin.register(CompanyRole)
class CompanyRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_system", "created_at")
    list_filter = ("is_system",)
    search_fields = ("name", "company__name")


@admin.register(CompanyPermission)
class CompanyPermissionAdmin(admin.ModelAdmin):
    list_display = ("module", "permission")
    list_filter = ("permission", "module")
    search_fields = ("module",)


@admin.register(MemberPermission)
class MemberPermissionAdmin(admin.ModelAdmin):
    list_display = ("member", "permission")
    search_fields = ("member__user__email", "permission__module")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "module", "can_read", "can_write", "can_edit", "can_delete", "can_admin")
    list_filter = ("module", "can_read", "can_admin")
    search_fields = ("role__name", "module")
