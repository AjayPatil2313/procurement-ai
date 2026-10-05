from django.urls import path
from .views import (
    RequirementListCreateAPIView,
    RequirementDetailAPIView,
)

urlpatterns = [
    path("", RequirementListCreateAPIView.as_view(), name="api-requirements-list"),
    path("<int:pk>/", RequirementDetailAPIView.as_view(), name="api-requirements-detail"),
]
