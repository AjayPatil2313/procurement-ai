from django.urls import path
from apps.billing.webhook_views import (
    create_payment_order_api,
    verify_payment_api,
    razorpay_webhook_view,
    stripe_webhook_view,
)
from apps.billing.pdf_views import (
    invoice_pdf_view,
    search_job_dossier_pdf_view,
)
from apps.billing.api_views import (
    company_wallet_api,
    wallet_transactions_api,
    wallet_recharge_api,
    usage_history_api,
    company_invoices_api,
    service_pricing_api,
    admin_wallets_list_api,
    admin_manual_adjustment_api,
    admin_service_pricing_config_api,
)

urlpatterns = [
    # PDF Downloads
    path("invoices/<int:invoice_id>/pdf/", invoice_pdf_view, name="invoice-pdf-download"),
    path("dossier/<int:job_id>/pdf/", search_job_dossier_pdf_view, name="search-job-dossier-pdf"),

    # Payment Gateway API & Webhooks
    path("create-order/", create_payment_order_api, name="billing-create-order"),
    path("verify-payment/", verify_payment_api, name="billing-verify-payment"),
    path("webhook/razorpay/", razorpay_webhook_view, name="billing-razorpay-webhook"),
    path("webhook/stripe/", stripe_webhook_view, name="billing-stripe-webhook"),
    path("webhook/", razorpay_webhook_view, name="billing-webhook-universal"),

    # REST API Endpoints (Company Wallet, Ledger & Usage)
    path("wallet/", company_wallet_api, name="billing-wallet-api"),
    path("transactions/", wallet_transactions_api, name="billing-transactions-api"),
    path("recharge/", wallet_recharge_api, name="billing-recharge-api"),
    path("usage/", usage_history_api, name="billing-usage-api"),
    path("invoices/", company_invoices_api, name="billing-invoices-api"),
    path("pricing/", service_pricing_api, name="billing-pricing-api"),

    # Super Admin Billing Management APIs
    path("admin/wallets/", admin_wallets_list_api, name="admin-billing-wallets-api"),
    path("admin/adjustments/", admin_manual_adjustment_api, name="admin-billing-adjustments-api"),
    path("admin/pricing/", admin_service_pricing_config_api, name="admin-billing-pricing-api"),
]
