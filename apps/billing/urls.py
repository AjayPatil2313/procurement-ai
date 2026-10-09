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

urlpatterns = [
    # PDF Downloads
    path("invoices/<int:invoice_id>/pdf/", invoice_pdf_view, name="invoice-pdf-download"),
    path("dossier/<int:job_id>/pdf/", search_job_dossier_pdf_view, name="search-job-dossier-pdf"),

    # Payment Gateway API & Webhooks
    path("create-order/", create_payment_order_api, name="billing-create-order"),
    path("verify-payment/", verify_payment_api, name="billing-verify-payment"),
    path("webhook/razorpay/", razorpay_webhook_view, name="billing-razorpay-webhook"),
    path("webhook/stripe/", stripe_webhook_view, name="billing-stripe-webhook"),
]
