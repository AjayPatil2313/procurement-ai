from rest_framework import serializers
from .models import SavedItem, Inquiry


class SavedItemSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    product_title = serializers.CharField(source="search_result.product_title", read_only=True)

    class Meta:
        model = SavedItem
        fields = [
            "id",
            "company",
            "company_name",
            "search_result",
            "product_title",
            "status",
            "notes",
            "assigned_to",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class InquirySerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "company",
            "company_name",
            "search_result",
            "subject",
            "message",
            "sent_to_email",
            "status",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "sent_at", "created_at", "updated_at"]
