from rest_framework import serializers
from .models import Product, Category


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent", "hsn_code"]


class ProductSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "company",
            "company_name",
            "type",
            "name",
            "category",
            "description",
            "specifications",
            "price_min",
            "price_max",
            "currency",
            "unit",
            "moq",
            "target_industries",
            "target_regions",
            "search_scope",
            "radius_km",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company",
            "created_at",
            "updated_at",
        ]
