from django.contrib import admin
from apps.leads.models import SavedItem, Inquiry, InquiryMessage, PriceHistory


@admin.register(SavedItem)
class SavedItemAdmin(admin.ModelAdmin):
    list_display = ("company", "search_result", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("company__name",)
    ordering = ("-created_at",)


class InquiryMessageInline(admin.TabularInline):
    model = InquiryMessage
    extra = 1


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "sent_to_email", "subject", "status", "quoted_price", "quoted_currency", "sent_at")
    list_filter = ("status", "quoted_currency")
    search_fields = ("sent_to_email", "subject", "message", "company__name")
    inlines = [InquiryMessageInline]
    ordering = ("-sent_at",)


@admin.register(InquiryMessage)
class InquiryMessageAdmin(admin.ModelAdmin):
    list_display = ("inquiry", "sender_name", "message_type", "subject", "created_at")
    list_filter = ("message_type",)
    search_fields = ("subject", "body", "sender_name")
    ordering = ("-created_at",)


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("external_company", "item_name", "price", "currency", "recorded_at")
    search_fields = ("item_name", "external_company__name")
    ordering = ("-recorded_at",)
