from django.contrib import admin
from apps.dashboard.models import ActivityLog, SupportTicket, Notification


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "user", "activity_type", "created_at")
    list_filter = ("activity_type",)
    search_fields = ("title", "description", "company__name", "user__email")
    ordering = ("-created_at",)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "subject", "company", "category", "priority", "status", "created_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("ticket_number", "subject", "description", "company__name", "contact_email")
    ordering = ("-created_at",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "company", "notification_type", "is_read", "created_at")
    list_filter = ("notification_type", "is_read")
    search_fields = ("title", "message", "user__email", "company__name")
    ordering = ("-created_at",)
