from django.urls import path
from .views import (
    CompanyProfileAPIView,
    CreateCompanyUserAPIView,
    CompanyUserListAPIView,
    AssignPermissionsAPIView,
    UserPermissionsAPIView,
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
   path(
    "users/permissions/",
    AssignPermissionsAPIView.as_view(),
    ),
    path(
    "users/<int:user_id>/permissions/",
    UserPermissionsAPIView.as_view(),
    ),
]