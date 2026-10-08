from django.contrib import admin
from apps.catalog.models import Category, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "hsn_code")
    search_fields = ("name", "hsn_code")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "category", "price", "currency", "availability", "created_at")
    list_filter = ("availability", "category", "currency")
    search_fields = ("name", "description", "company__name")
    inlines = [ProductImageInline]
    ordering = ("-created_at",)
