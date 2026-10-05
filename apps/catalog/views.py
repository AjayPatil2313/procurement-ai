from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product
from .serializers import ProductSerializer
from apps.companies.rbac import (
    IsSellerCompany,
    HasModulePermission,
    get_user_rbac_context,
)


class ProductListCreateAPIView(APIView):
    """
    Seller REST API:
    - GET: List company's products (requires READ permission)
    - POST: Create new product (requires EDIT permission)
    Enforces that the user belongs to a SELLER or BOTH company.
    """
    permission_classes = [IsAuthenticated, IsSellerCompany, HasModulePermission]
    required_module = "products"

    def get(self, request):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")

        if rbac["is_super_admin"] and not company:
            products = Product.objects.all().select_related("company", "category")
        else:
            products = Product.objects.filter(company=company).select_related("category")

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
        prod = serializer.save(company=company)
        return Response(
            ProductSerializer(prod).data,
            status=status.HTTP_201_CREATED,
        )


class ProductDetailAPIView(APIView):
    """
    Seller REST API for single product:
    - GET: Retrieve product (READ)
    - PUT/PATCH: Update product (UPDATE)
    - DELETE: Delete product (DELETE)
    """
    permission_classes = [IsAuthenticated, IsSellerCompany, HasModulePermission]
    required_module = "products"

    def get_object(self, request, pk):
        rbac = get_user_rbac_context(request.user)
        company = rbac.get("company")
        if rbac["is_super_admin"]:
            return get_object_or_404(Product, pk=pk)
        return get_object_or_404(Product, pk=pk, company=company)

    def get(self, request, pk):
        prod = self.get_object(request, pk)
        return Response(ProductSerializer(prod).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        prod = self.get_object(request, pk)
        serializer = ProductSerializer(prod, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        prod = self.get_object(request, pk)
        serializer = ProductSerializer(prod, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        prod = self.get_object(request, pk)
        prod.delete()
        return Response(
            {"message": "Product deleted successfully."},
            status=status.HTTP_204_NO_CONTENT,
        )
