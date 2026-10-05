from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductImage, Category
from .serializers import ProductSerializer, CategorySerializer
from apps.companies.rbac import (
    IsSellerCompany,
    HasModulePermission,
    get_user_rbac_context,
)


class CategoryListAPIView(APIView):
    """
    List product categories
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = Category.objects.all().order_by("name")
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ProductListCreateAPIView(APIView):
    """
    Seller REST API:
    - GET: List company's active products (requires READ permission)
    - POST: Create new product (requires EDIT permission)
    Enforces that the user belongs to a SELLER or BOTH company.
    Strict multi-tenant data isolation by company.
    """
    permission_classes = [IsAuthenticated, IsSellerCompany, HasModulePermission]
    required_module = "products"

    def get(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")

        if rbac["is_super_admin"] and not company:
            products = Product.objects.filter(is_deleted=False).select_related("company", "category").prefetch_related("images")
        else:
            products = Product.objects.filter(company=company, is_deleted=False).select_related("category").prefetch_related("images")

        # Optional filters
        q = request.query_params.get("q", "").strip()
        if q:
            products = products.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(specifications__icontains=q)
                | Q(location__icontains=q)
            )

        category_id = request.query_params.get("category")
        if category_id:
            products = products.filter(category_id=category_id)

        availability = request.query_params.get("availability")
        if availability:
            products = products.filter(availability=availability)

        serializer = ProductSerializer(products, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if not company:
            return Response(
                {"error": "No active company found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prod = serializer.save(company=company, created_by=request.user)

        # Handle image_url if supplied
        image_url = request.data.get("image_url", "").strip()
        if image_url:
            ProductImage.objects.create(
                product=prod,
                image_url=image_url,
                is_primary=True,
            )

        return Response(
            ProductSerializer(prod).data,
            status=status.HTTP_201_CREATED,
        )


class ProductDetailAPIView(APIView):
    """
    Seller REST API for single product:
    - GET: Retrieve product (READ)
    - PUT/PATCH: Update product (UPDATE/EDIT)
    - DELETE: Soft delete product (DELETE)
    Strict company multi-tenant isolation.
    """
    permission_classes = [IsAuthenticated, IsSellerCompany, HasModulePermission]
    required_module = "products"

    def get_object(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if rbac["is_super_admin"]:
            return get_object_or_404(Product, pk=pk, is_deleted=False)
        return get_object_or_404(Product, pk=pk, company=company, is_deleted=False)

    def get(self, request, pk):
        prod = self.get_object(request, pk)
        return Response(ProductSerializer(prod).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        prod = self.get_object(request, pk)
        serializer = ProductSerializer(prod, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        image_url = request.data.get("image_url", "").strip()
        if image_url:
            primary_img = prod.images.filter(is_primary=True).first()
            if primary_img:
                primary_img.image_url = image_url
                primary_img.save()
            else:
                ProductImage.objects.create(product=prod, image_url=image_url, is_primary=True)

        return Response(ProductSerializer(prod).data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        prod = self.get_object(request, pk)
        serializer = ProductSerializer(prod, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        image_url = request.data.get("image_url", "").strip()
        if image_url:
            primary_img = prod.images.filter(is_primary=True).first()
            if primary_img:
                primary_img.image_url = image_url
                primary_img.save()
            else:
                ProductImage.objects.create(product=prod, image_url=image_url, is_primary=True)

        return Response(ProductSerializer(prod).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        prod = self.get_object(request, pk)
        # B2B soft-delete to preserve references and transaction logs
        prod.soft_delete()
        return Response(
            {
                "message": "Product soft-deleted successfully.",
                "id": prod.id,
                "is_deleted": True,
            },
            status=status.HTTP_200_OK,
        )
