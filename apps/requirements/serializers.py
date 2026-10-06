from rest_framework import serializers
from .models import Requirement


class RequirementSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)

    class Meta:
        model = Requirement
        fields = [
            "id",
            "company",
            "company_name",
            "created_by",
            "created_by_email",
            "item_name",
            "category",
            "category_name",
            "description",
            "specifications",
            "quantity",
            "unit",
            "target_price",
            "currency",
            "delivery_city",
            "delivery_country",
            "required_by",
            "search_scope",
            "radius_km",
            "status",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company",
            "created_by",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
