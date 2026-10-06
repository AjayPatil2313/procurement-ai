from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

from apps.accounts.web_views import (
    login_view,
    logout_view,
    register_view,
    forgot_password_view,
    reset_password_view,
)
from apps.dashboard.views import global_search_view
from apps.companies.web_views import (
    company_profile_web_view,
    company_team_web_view,
    company_rbac_web_view,
    subscription_billing_web_view,
)
from apps.requirements.web_views import (
    requirements_list_view,
    requirement_create_view,
    requirement_detail_view,
    requirement_edit_view,
    requirement_delete_view,
    find_suppliers_view,
)
from apps.catalog.web_views import (
    products_list_view,
    product_create_view,
    product_detail_view,
    product_edit_view,
    product_delete_view,
    find_buyers_view,
)
from apps.leads.web_views import (
    saved_suppliers_view,
    saved_leads_view,
    inquiries_list_view,
    price_comparison_view,
    leads_list_view,
    export_reports_view,
)
from apps.companies.admin_views import (
    admin_panel_companies_view,
    admin_panel_users_view,
    admin_panel_apilogs_view,
)

urlpatterns = [
    # Django Builtin Admin
    path("admin/", admin.site.urls),

    # Web Auth
    path("login/", login_view, name="login"),
    path("register/", register_view, name="register"),
    path("signup/", lambda request: redirect("register"), name="signup"),
    path("logout/", logout_view, name="logout-web"),
    path("forgot-password/", forgot_password_view, name="forgot-password"),
    path("reset-password/<str:token>/", reset_password_view, name="reset-password"),

    # Root redirect: Login first, then enter project
    path("", lambda request: redirect("dashboard") if request.user.is_authenticated else redirect("login"), name="root-redirect"),

    # Dashboard & Switchers
    path("dashboard/", include("apps.dashboard.urls")),
    path("search/", global_search_view, name="global-search"),

    # Company Management (Web)
    path("company/profile/", company_profile_web_view, name="company-profile-web"),
    path("company/team/", company_team_web_view, name="company-team-web"),
    path("company/rbac/", company_rbac_web_view, name="company-rbac-web"),
    path("company/billing/", subscription_billing_web_view, name="subscription-billing-web"),

    # Buyer / Procurement (Web)
    path("procurement/find-suppliers/", find_suppliers_view, name="find-suppliers"),
    path("procurement/requirements/", requirements_list_view, name="requirements-list"),
    path("procurement/requirements/create/", requirement_create_view, name="requirements-create"),
    path("procurement/requirements/<int:pk>/", requirement_detail_view, name="requirement-detail"),
    path("procurement/requirements/<int:pk>/edit/", requirement_edit_view, name="requirement-edit"),
    path("procurement/requirements/<int:pk>/delete/", requirement_delete_view, name="requirement-delete"),
    path("procurement/saved-suppliers/", saved_suppliers_view, name="saved-suppliers"),
    path("procurement/inquiries/", inquiries_list_view, name="inquiries-list"),
    path("procurement/price-comparison/", price_comparison_view, name="price-comparison"),
    path("procurement/export-reports/", export_reports_view, name="export-reports"),

    # Seller / Sales (Web)
    path("sales/find-buyers/", find_buyers_view, name="find-buyers"),
    path("sales/products/", products_list_view, name="products-list"),
    path("sales/products/create/", product_create_view, name="products-create"),
    path("sales/products/<int:pk>/", product_detail_view, name="product-detail"),
    path("sales/products/<int:pk>/edit/", product_edit_view, name="product-edit"),
    path("sales/products/<int:pk>/delete/", product_delete_view, name="product-delete"),
    path("sales/leads/", leads_list_view, name="leads-list"),
    path("sales/saved-leads/", saved_leads_view, name="saved-leads"),
    path("sales/export-reports/", export_reports_view, name="sales-export-reports"),

    # Super Admin Panel (Web)
    path("admin-panel/companies/", admin_panel_companies_view, name="admin-panel-companies"),
    path("admin-panel/users/", admin_panel_users_view, name="admin-panel-users"),
    path("admin-panel/api-logs/", admin_panel_apilogs_view, name="admin-panel-apilogs"),

    # REST APIs
    path("api/auth/", include("apps.accounts.urls")),
    path("api/companies/", include("apps.companies.urls")),
    path("api/requirements/", include("apps.requirements.urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/leads/", include("apps.leads.urls")),
]
