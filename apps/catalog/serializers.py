from rest_framework import serializers
from .models import Product, ProductImage, Category


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent", "hsn_code"]


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "image_url", "is_primary", "created_at"]


class ProductSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    created_by_email = serializers.CharField(source="created_by.email", read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "company",
            "company_name",
            "name",
            "category",
            "category_name",
            "description",
            "specifications",
            "price",
            "price_min",
            "price_max",
            "currency",
            "minimum_order_quantity",
            "moq",
            "unit",
            "availability",
            "location",
            "images",
            "created_by",
            "created_by_email",
            "type",
            "target_industries",
            "target_regions",
            "search_scope",
            "radius_km",
            "is_active",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company",
            "company_name",
            "category_name",
            "created_by",
            "created_by_email",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
