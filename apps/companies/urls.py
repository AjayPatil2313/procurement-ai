from django.urls import path
from .views import (
    CompanyProfileAPIView,
    CreateCompanyUserAPIView,
    CompanyUserListAPIView,
    CompanyUserDetailUpdateAPIView,
    AssignPermissionsAPIView,
    UserPermissionsAPIView,
    CompanyModulesAPIView,
)

urlpatterns = [
    path(
        "<int:company_id>/profile/",
        CompanyProfileAPIView.as_view(),
        name="company-profile",
    ),
    path(
        "modules/",
        CompanyModulesAPIView.as_view(),
        name="company-modules",
    ),
    path(
        "users/create/",
        CreateCompanyUserAPIView.as_view(),
        name="company-users-create",
    ),
    path(
        "users/",
        CompanyUserListAPIView.as_view(),
        name="company-users-list",
    ),
    path(
        "users/<int:user_id>/",
        CompanyUserDetailUpdateAPIView.as_view(),
        name="company-user-detail-update",
    ),
    path(
        "users/permissions/",
        AssignPermissionsAPIView.as_view(),
        name="company-users-permissions-assign",
    ),
    path(
        "users/<int:user_id>/permissions/",
        UserPermissionsAPIView.as_view(),
        name="company-user-permissions-detail",
    ),
]