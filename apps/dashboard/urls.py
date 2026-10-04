from django.urls import path
from .views import (
    dashboard_view,
    DashboardAPIView,
    switch_company_view,
    demo_login_as_view,
    global_search_view,
)

urlpatterns = [
    path("", dashboard_view, name="dashboard"),
    path("api/dashboard/", DashboardAPIView.as_view(), name="api-dashboard"),
    path("switch-company/", switch_company_view, name="switch-company"),
    path("demo-role/<str:role_name>/", demo_login_as_view, name="demo-login-as"),
    path("search/", global_search_view, name="global-search"),
]
