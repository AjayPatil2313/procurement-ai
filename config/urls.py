from django.conf import settings
from django.conf.urls.static import static
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
from apps.dashboard.views import (
    global_search_view,
    help_support_view,
    create_support_ticket_view,
    support_ticket_detail_view,
    update_support_ticket_view,
    create_faq_view,
    edit_faq_view,
    delete_faq_view,
)
from apps.companies.web_views import (
    company_profile_web_view,
    company_team_web_view,
    company_rbac_web_view,
    subscription_billing_web_view,
    seller_roles_web_view,
    seller_role_create_view,
    seller_role_edit_view,
    seller_role_delete_view,
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
    inquiry_detail_view,
    record_inquiry_quote_view,
    resend_inquiry_view,
    delete_inquiry_view,
    add_inquiry_message_view,
    update_inquiry_status_view,
    price_comparison_view,
    leads_list_view,
    export_reports_view,
    save_lead_toggle_view,
    update_saved_lead_view,
    delete_saved_lead_view,
    save_supplier_toggle_view,
    delete_saved_supplier_view,
    send_rfq_view,
)
from apps.companies.admin_views import (
    admin_panel_dashboard_view,
    admin_panel_companies_view,
    admin_panel_company_create_view,
    admin_panel_company_detail_view,
    admin_panel_company_edit_view,
    admin_panel_toggle_company_status_view,
    admin_panel_toggle_company_verification_view,
    admin_panel_company_delete_view,
    admin_panel_company_restore_view,
    admin_panel_plans_view,
    admin_panel_update_subscription_view,
    admin_panel_users_view,
    admin_panel_toggle_user_active_view,
    admin_panel_reset_company_user_password_view,
    admin_panel_toggle_maintenance_view,
    admin_panel_apilogs_view,
    admin_panel_auditlogs_view,
)
from apps.ai_search.web_views import (
    matching_parameters_view,
    matching_parameter_create_view,
    matching_parameter_edit_view,
    matching_parameter_delete_view,
    matching_parameter_toggle_view,
    matching_parameter_reset_defaults_view,
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
    path("help/", help_support_view, name="help-support"),
    path("help/ticket/create/", create_support_ticket_view, name="help-ticket-create"),
    path("help/ticket/<int:pk>/", support_ticket_detail_view, name="help-ticket-detail"),
    path("help/ticket/<int:pk>/update/", update_support_ticket_view, name="help-ticket-update"),
    path("help/faq/create/", create_faq_view, name="help-faq-create"),
    path("help/faq/<int:pk>/edit/", edit_faq_view, name="help-faq-edit"),
    path("help/faq/<int:pk>/delete/", delete_faq_view, name="help-faq-delete"),

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
    path("procurement/suppliers/save/<int:result_id>/", save_supplier_toggle_view, name="save-supplier-toggle"),
    path("procurement/saved-suppliers/", saved_suppliers_view, name="saved-suppliers"),
    path("procurement/saved-suppliers/<int:item_id>/delete/", delete_saved_supplier_view, name="delete-saved-supplier"),
    path("procurement/inquiries/", inquiries_list_view, name="inquiries-list"),
    path("procurement/inquiries/<int:pk>/", inquiry_detail_view, name="inquiry-detail"),
    path("procurement/inquiries/<int:pk>/message/", add_inquiry_message_view, name="inquiry-add-message"),
    path("procurement/inquiries/<int:pk>/status/", update_inquiry_status_view, name="inquiry-update-status"),
    path("procurement/inquiries/<int:pk>/quote/", record_inquiry_quote_view, name="record-inquiry-quote"),
    path("procurement/inquiries/<int:pk>/resend/", resend_inquiry_view, name="resend-inquiry"),
    path("procurement/inquiries/<int:pk>/delete/", delete_inquiry_view, name="delete-inquiry"),
    path("procurement/inquiries/send/<int:result_id>/", send_rfq_view, name="send-rfq"),
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
    path("sales/leads/save/<int:result_id>/", save_lead_toggle_view, name="save-lead-toggle"),
    path("sales/saved-leads/", saved_leads_view, name="saved-leads"),
    path("sales/saved-leads/<int:item_id>/update/", update_saved_lead_view, name="update-saved-lead"),
    path("sales/saved-leads/<int:item_id>/delete/", delete_saved_lead_view, name="delete-saved-lead"),
    path("sales/inquiries/", inquiries_list_view, name="sales-inquiries-list"),
    path("sales/inquiries/<int:pk>/", inquiry_detail_view, name="sales-inquiry-detail"),
    path("sales/inquiries/<int:pk>/message/", add_inquiry_message_view, name="sales-inquiry-add-message"),
    path("sales/inquiries/<int:pk>/status/", update_inquiry_status_view, name="sales-inquiry-update-status"),
    path("sales/inquiries/<int:pk>/quote/", record_inquiry_quote_view, name="sales-record-inquiry-quote"),
    path("sales/inquiries/<int:pk>/resend/", resend_inquiry_view, name="sales-resend-inquiry"),
    path("sales/inquiries/<int:pk>/delete/", delete_inquiry_view, name="sales-delete-inquiry"),
    path("sales/inquiries/send/<int:result_id>/", send_rfq_view, name="sales-send-inquiry"),
    path("sales/export-reports/", export_reports_view, name="sales-export-reports"),
    path("sales/roles/", seller_roles_web_view, name="seller-roles-web"),
    path("sales/roles/create/", seller_role_create_view, name="seller-role-create"),
    path("sales/roles/<int:role_id>/edit/", seller_role_edit_view, name="seller-role-edit"),
    path("sales/roles/<int:role_id>/delete/", seller_role_delete_view, name="seller-role-delete"),
    path("sales/matching-parameters/", matching_parameters_view, name="matching-parameters"),
    path("sales/matching-parameters/create/", matching_parameter_create_view, name="matching-parameter-create"),
    path("sales/matching-parameters/<int:pk>/edit/", matching_parameter_edit_view, name="matching-parameter-edit"),
    path("sales/matching-parameters/<int:pk>/delete/", matching_parameter_delete_view, name="matching-parameter-delete"),
    path("sales/matching-parameters/<int:pk>/toggle/", matching_parameter_toggle_view, name="matching-parameter-toggle"),
    path("sales/matching-parameters/reset/", matching_parameter_reset_defaults_view, name="matching-parameter-reset"),

    # Super Admin Panel (Web)
    path("admin-panel/", admin_panel_dashboard_view, name="admin-panel-dashboard"),
    path("admin-panel/dashboard/", admin_panel_dashboard_view, name="admin-panel-dashboard-alias"),
    path("admin-panel/companies/", admin_panel_companies_view, name="admin-panel-companies"),
    path("admin-panel/companies/create/", admin_panel_company_create_view, name="admin-panel-company-create"),
    path("admin-panel/companies/<int:pk>/", admin_panel_company_detail_view, name="admin-panel-company-detail"),
    path("admin-panel/companies/<int:pk>/edit/", admin_panel_company_edit_view, name="admin-panel-company-edit"),
    path("admin-panel/companies/<int:pk>/delete/", admin_panel_company_delete_view, name="admin-panel-company-delete"),
    path("admin-panel/companies/<int:pk>/restore/", admin_panel_company_restore_view, name="admin-panel-company-restore"),
    path("admin-panel/companies/<int:pk>/toggle-status/", admin_panel_toggle_company_status_view, name="admin-panel-toggle-company-status"),
    path("admin-panel/companies/<int:pk>/toggle-verification/", admin_panel_toggle_company_verification_view, name="admin-panel-toggle-company-verification"),
    path("admin-panel/plans/", admin_panel_plans_view, name="admin-panel-plans"),
    path("admin-panel/plans/<int:company_id>/update/", admin_panel_update_subscription_view, name="admin-panel-update-subscription"),
    path("admin-panel/users/", admin_panel_users_view, name="admin-panel-users"),
    path("admin-panel/users/<int:pk>/toggle-active/", admin_panel_toggle_user_active_view, name="admin-panel-toggle-user-active"),
    path("admin-panel/companies/<int:company_id>/users/<int:user_id>/reset-password/", admin_panel_reset_company_user_password_view, name="admin-panel-reset-user-password"),
    path("admin-panel/maintenance/toggle/", admin_panel_toggle_maintenance_view, name="admin-panel-toggle-maintenance"),
    path("admin-panel/api-logs/", admin_panel_apilogs_view, name="admin-panel-apilogs"),
    path("admin-panel/audit-logs/", admin_panel_auditlogs_view, name="admin-panel-auditlogs"),

    # REST APIs
    path("api/auth/", include("apps.accounts.urls")),
    path("api/companies/", include("apps.companies.urls")),
    path("api/requirements/", include("apps.requirements.urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/leads/", include("apps.leads.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

