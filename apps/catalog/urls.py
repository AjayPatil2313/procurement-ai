from django.urls import path
from .views import (
    ProductListCreateAPIView,
    ProductDetailAPIView,
    CategoryListAPIView,
)

urlpatterns = [
    path("categories/", CategoryListAPIView.as_view(), name="api-category-list"),
    path("products/", ProductListCreateAPIView.as_view(), name="api-products-list"),
    path("products/<int:pk>/", ProductDetailAPIView.as_view(), name="api-products-detail"),
]
