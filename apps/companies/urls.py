from django.urls import path
from .views import (
    CompanyProfileAPIView,
    CreateCompanyUserAPIView,
    CompanyUserListAPIView,
)
urlpatterns = [
    path(
        "<int:company_id>/profile/",
        CompanyProfileAPIView.as_view(),
        name="company-profile",
    ),
    
    path(
    "users/create/",
    CreateCompanyUserAPIView.as_view(),
    ),

    path(
    "users/",
    CompanyUserListAPIView.as_view(),
),
]