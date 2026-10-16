import csv
import json
import logging
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.companies.rbac import superadmin_required
from apps.notifications.models import (
    NotificationTemplate,
    NotificationRule,
    NotificationLog,
    EmailDeliveryAttempt,
    SMTPSetting,
    EmailLayoutSetting,
    DeviceToken,
    PushDeliveryLog,
)
from apps.notifications.services.email_engine import (
    render_string_template,
    build_branded_html,
    retry_failed_notification_log,
)
from apps.notifications.services.smtp_service import test_smtp_connection

logger = logging.getLogger(__name__)


def paginate_and_preserve(request, queryset, per_page=10):
    requested_per_page = request.GET.get("per_page")
    if requested_per_page:
        try:
            per_page = max(5, min(100, int(requested_per_page)))
        except ValueError:
            pass

    paginator = Paginator(queryset, per_page)
    page = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    params = request.GET.copy()
    if "page" in params:
        del params["page"]
    query_string = params.urlencode()
    return page_obj, query_string


@login_required
@superadmin_required
def admin_email_overview_view(request):
    """
    Super Admin Email Operations Dashboard:
    Real-time delivery statistics, status distribution, 24-hour trends, and recent transmissions.
    """
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    total_logs = NotificationLog.objects.count()
    sent_count = NotificationLog.objects.filter(status=NotificationLog.Status.SENT).count()
    failed_count = NotificationLog.objects.filter(status=NotificationLog.Status.FAILED).count()
    queued_count = NotificationLog.objects.filter(
        status__in=[NotificationLog.Status.QUEUED, NotificationLog.Status.SENDING]
    ).count()

    delivery_rate = round((sent_count / total_logs * 100), 1) if total_logs > 0 else 100.0

    # 24h metrics
    logs_24h = NotificationLog.objects.filter(created_at__gte=last_24h)
    sent_24h = logs_24h.filter(status=NotificationLog.Status.SENT).count()
    failed_24h = logs_24h.filter(status=NotificationLog.Status.FAILED).count()

    # Active SMTP config
    active_smtp = SMTPSetting.get_active_config()

    # Total active templates & rules
    active_templates_count = NotificationTemplate.objects.filter(is_active=True).count()
    active_rules_count = NotificationRule.objects.filter(is_enabled=True).count()

    # Recent 10 logs
    recent_logs = NotificationLog.objects.select_related("company", "user", "template").order_by("-created_at")[:10]

    # Category breakdown
    template_categories = NotificationTemplate.objects.values("category").annotate(count=Count("id")).order_by("category")

    # Retry pending in backoff
    retry_pending_count = NotificationLog.objects.filter(
        status=NotificationLog.Status.QUEUED,
        retry_after__gt=timezone.now(),
    ).count()
    has_attachments_count = NotificationLog.objects.filter(has_attachments=True).count()

    # Category delivery breakdown
    category_logs = (
        NotificationLog.objects.filter(template__isnull=False)
        .values("template__category")
        .annotate(
            total=Count("id"),
            sent=Count("id", filter=Q(status=NotificationLog.Status.SENT)),
            failed=Count("id", filter=Q(status=NotificationLog.Status.FAILED)),
        )
        .order_by("-total")
    )

    context = {
        "page_title": "Email Operations Overview",
        "active_tab": "overview",
        "total_logs": total_logs,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "queued_count": queued_count,
        "retry_pending_count": retry_pending_count,
        "has_attachments_count": has_attachments_count,
        "delivery_rate": delivery_rate,
        "sent_24h": sent_24h,
        "failed_24h": failed_24h,
        "active_smtp": active_smtp,
        "active_templates_count": active_templates_count,
        "active_rules_count": active_rules_count,
        "recent_logs": recent_logs,
        "template_categories": template_categories,
        "category_logs": category_logs,
    }
    return render(request, "admin_panel/email/overview.html", context)


@login_required
@superadmin_required
def admin_email_templates_view(request):
    """
    Super Admin Template Management:
    Lists all email notification templates with filtering by category and search.
    """
    category_filter = request.GET.get("category", "").strip().upper()
    search_query = request.GET.get("q", "").strip()

    templates_qs = NotificationTemplate.objects.all().order_by("category", "name")

    if category_filter:
        templates_qs = templates_qs.filter(category=category_filter)
    if search_query:
        templates_qs = templates_qs.filter(
            Q(name__icontains=search_query)
            | Q(event_key__icontains=search_query)
            | Q(subject__icontains=search_query)
        )

    categories = NotificationTemplate.Category.choices
    page_obj, query_string = paginate_and_preserve(request, templates_qs, per_page=10)

    context = {
        "page_title": "Notification Templates",
        "active_tab": "templates",
        "templates": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "categories": categories,
        "selected_category": category_filter,
        "search_query": search_query,
    }
    return render(request, "admin_panel/email/templates.html", context)


@login_required
@superadmin_required
def admin_email_template_edit_view(request, pk):
    """
    Super Admin Template Editor:
    Edit subject, HTML body, plain text content, and active status.
    """
    template_obj = get_object_or_404(NotificationTemplate, pk=pk)
    active_layout = EmailLayoutSetting.get_active_layout()

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        html_content = request.POST.get("html_content", "").strip()
        text_content = request.POST.get("text_content", "").strip()
        is_active = request.POST.get("is_active") == "on"
        use_custom_layout = request.POST.get("use_custom_layout") == "on"
        header_html = request.POST.get("header_html", "").strip()
        footer_html = request.POST.get("footer_html", "").strip()

        if not subject:
            messages.error(request, "Subject line is required.")
        elif not html_content:
            messages.error(request, "HTML content is required.")
        else:
            # Pre-save Django Template Syntax Validation
            from django.template import Template, TemplateSyntaxError
            syntax_error = None
            try:
                Template(html_content)
            except TemplateSyntaxError as err:
                syntax_error = f"HTML Content Syntax Error: {err}"
            except Exception as err:
                syntax_error = f"HTML Content Error: {err}"

            if not syntax_error and subject:
                try:
                    Template(subject)
                except TemplateSyntaxError as err:
                    syntax_error = f"Subject Syntax Error: {err}"
                except Exception as err:
                    syntax_error = f"Subject Error: {err}"

            if syntax_error:
                messages.error(request, syntax_error)
                template_obj.subject = subject
                template_obj.html_content = html_content
                template_obj.text_content = text_content
                template_obj.header_html = header_html
                template_obj.footer_html = footer_html
            else:
                template_obj.subject = subject
                template_obj.html_content = html_content
                template_obj.text_content = text_content
                template_obj.use_custom_layout = use_custom_layout
                template_obj.header_html = header_html
                template_obj.footer_html = footer_html
                # Guard system templates from being deactivated casually
                if not template_obj.is_system or is_active:
                    template_obj.is_active = is_active
                template_obj.version += 1
                template_obj.save()

                messages.success(request, f"Template '{template_obj.name}' updated successfully (v{template_obj.version}).")
                return redirect("admin-panel-email-templates")

    context = {
        "page_title": f"Edit Template: {template_obj.name}",
        "active_tab": "templates",
        "template_obj": template_obj,
        "active_layout": active_layout,
    }
    return render(request, "admin_panel/email/template_edit.html", context)


@login_required
@superadmin_required
def admin_email_layout_view(request):
    """
    Super Admin Master Email Header & Footer Layout Management:
    Allows full control over the standard header, trust badges, and dark brand footer
    shared across all responsive email templates, with live real-time preview.
    """
    layout_obj = EmailLayoutSetting.get_active_layout()

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action == "reset":
            # Reset to platform standard defaults
            layout_obj.brand_name = "TradeConnect"
            layout_obj.brand_tagline = "Buy • Sell • Grow"
            layout_obj.header_title = "B2B Procurement & Sales Platform"
            layout_obj.header_subtitle = "Smarter Sourcing. Stronger Partnerships."
            layout_obj.logo_url = ""
            layout_obj.header_bg_color = "#ffffff"
            layout_obj.header_text_color = "#0f172a"
            layout_obj.use_custom_header_html = False
            layout_obj.custom_header_html = ""
            layout_obj.show_trust_badges = True
            layout_obj.badge_1_icon = "🛡️"
            layout_obj.badge_1_text = "Secure & Verified"
            layout_obj.badge_2_icon = "☑️"
            layout_obj.badge_2_text = "Trusted B2B Platform"
            layout_obj.badge_3_icon = "🎧"
            layout_obj.badge_3_text = "24/7 Support"
            layout_obj.footer_brand_name = "TradeConnect"
            layout_obj.website_url = "https://tradeconnect.com"
            layout_obj.support_email = "support@tradeconnect.com"
            layout_obj.compliance_text = "You received this operational email because your company account is registered on the TradeConnect B2B AI Procurement & Sales Platform."
            layout_obj.copyright_text = "© 2026 TradeConnect Inc. All rights reserved."
            layout_obj.social_linkedin = "https://linkedin.com"
            layout_obj.social_facebook = "https://facebook.com"
            layout_obj.social_youtube = "https://youtube.com"
            layout_obj.social_twitter = "https://twitter.com"
            layout_obj.use_custom_footer_html = False
            layout_obj.custom_footer_html = ""
            layout_obj.updated_by = request.user
            layout_obj.save()
            messages.success(request, "Master Header & Footer layout reset to platform standard defaults.")
            return redirect("admin-panel-email-layout")

        # Save customized values
        layout_obj.brand_name = request.POST.get("brand_name", "").strip() or "TradeConnect"
        layout_obj.brand_tagline = request.POST.get("brand_tagline", "").strip() or "Buy • Sell • Grow"
        layout_obj.header_title = request.POST.get("header_title", "").strip() or "B2B Procurement & Sales Platform"
        layout_obj.header_subtitle = request.POST.get("header_subtitle", "").strip() or "Smarter Sourcing. Stronger Partnerships."
        layout_obj.logo_url = request.POST.get("logo_url", "").strip()
        layout_obj.header_bg_color = request.POST.get("header_bg_color", "#ffffff").strip() or "#ffffff"
        layout_obj.header_text_color = request.POST.get("header_text_color", "#0f172a").strip() or "#0f172a"
        layout_obj.use_custom_header_html = request.POST.get("use_custom_header_html") == "on"
        layout_obj.custom_header_html = request.POST.get("custom_header_html", "").strip()

        layout_obj.show_trust_badges = request.POST.get("show_trust_badges") == "on"
        layout_obj.badge_1_icon = request.POST.get("badge_1_icon", "🛡️").strip()
        layout_obj.badge_1_text = request.POST.get("badge_1_text", "Secure & Verified").strip()
        layout_obj.badge_2_icon = request.POST.get("badge_2_icon", "☑️").strip()
        layout_obj.badge_2_text = request.POST.get("badge_2_text", "Trusted B2B Platform").strip()
        layout_obj.badge_3_icon = request.POST.get("badge_3_icon", "🎧").strip()
        layout_obj.badge_3_text = request.POST.get("badge_3_text", "24/7 Support").strip()

        layout_obj.footer_brand_name = request.POST.get("footer_brand_name", "").strip() or layout_obj.brand_name
        layout_obj.website_url = request.POST.get("website_url", "").strip() or "https://tradeconnect.com"
        layout_obj.support_email = request.POST.get("support_email", "").strip() or "support@tradeconnect.com"
        layout_obj.compliance_text = request.POST.get("compliance_text", "").strip()
        layout_obj.copyright_text = request.POST.get("copyright_text", "").strip() or f"© {timezone.now().year} {layout_obj.brand_name}. All rights reserved."
        layout_obj.social_linkedin = request.POST.get("social_linkedin", "").strip()
        layout_obj.social_facebook = request.POST.get("social_facebook", "").strip()
        layout_obj.social_youtube = request.POST.get("social_youtube", "").strip()
        layout_obj.social_twitter = request.POST.get("social_twitter", "").strip()
        layout_obj.use_custom_footer_html = request.POST.get("use_custom_footer_html") == "on"
        layout_obj.custom_footer_html = request.POST.get("custom_footer_html", "").strip()

        layout_obj.updated_by = request.user
        layout_obj.save()

        messages.success(
            request,
            "Master Email Header & Footer layout updated successfully! All standard email templates will now use this branding."
        )
        return redirect("admin-panel-email-layout")

    # Generate live preview HTML for initial render
    sample_body = """
    <div style="margin: 0 0 20px 0;">
      <p style="font-size: 18px; font-weight: 800; color: #0f172a; margin: 0 0 8px 0; line-height: 1.2;">
        Hello Procurement Team,
      </p>
      <p style="font-size: 13px; color: #475569; line-height: 1.6; margin: 0;">
        This is a live preview demonstrating how your Master Header and Footer frame every transactional email body across the platform.
      </p>
    </div>
    <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin-bottom: 16px;">
      <div style="font-size: 13px; font-weight: 800; color: #0f172a; margin-bottom: 8px;">Sample Sourcing Requirement</div>
      <div style="font-size: 12px; color: #64748b; line-height: 1.5;">Industrial Stainless Steel Valves (Grade 316) &bull; 14 Verified Vendors Discovered</div>
    </div>
    """
    preview_html = build_branded_html(
        title="Master Layout Preview",
        summary="Live visual layout preview with active header and footer settings.",
        cta_url="https://tradeconnect.com/dashboard/",
        cta_text="Sample Action Button",
        severity="INFO",
        custom_content=sample_body,
        layout_setting=layout_obj,
    )

    context = {
        "page_title": "Master Email Header & Footer Layout",
        "active_tab": "layout",
        "layout_obj": layout_obj,
        "preview_html": preview_html,
    }
    return render(request, "admin_panel/email/layout.html", context)


@login_required
@superadmin_required
@require_POST
def admin_email_template_preview_api(request, pk):
    """
    JSON API for live template preview rendering with sample variables.
    """
    template_obj = get_object_or_404(NotificationTemplate, pk=pk)

    now = timezone.now()
    sample_context = {
        "recipient_name": request.user.get_full_name() or "Enterprise Partner",
        "recipient_email": request.user.email or "partner@enterprise.com",
        "company_name": "Sample Enterprise Ltd",
        "platform_name": "TradeConnect B2B AI Procurement & Sales",
        "current_year": now.year,
        # Template 1: Inquiry received
        "buyer_company": "ABC Industries Pvt. Ltd.",
        "contact_person": "Rahul Sharma",
        "buyer_email": "rahul@abcindustries.com",
        "buyer_phone": "+91 98765 43210",
        "buyer_location": "Ahmedabad, Gujarat",
        "company_type": "Private Limited (Pvt Ltd)",
        "product_name": "PP Woven Bag",
        "quantity": "10,000 Bags",
        "specification": "5 kg Capacity, Food Grade",
        "price_range": "₹ 18 - ₹ 22 per bag",
        # Template 2: Matching suppliers found
        "requirement_title": "Industrial Stainless Steel Valves (Grade 316)",
        "category": "Industrial Machinery & Valves",
        "delivery_timeline": "Within 15 Days",
        "count": "14",
        "top_supplier": "Apex Engineering Works",
        "verification_status": "GST & MSME Verified",
        "estimated_price": "₹15,000 / unit",
        # Template 3: New buyer lead
        "sourcing_location": "Pune, Maharashtra",
        "budget_range": "₹15,00,000 - ₹20,00,000",
        "lead_time": "Immediate (Within 2 Weeks)",
        "match_score": "96% High Match",
        # Template 4: Wallet recharge
        "amount": "25,000.00",
        "transaction_ref": "TXN-DEMO-88210",
        "payment_method": "Razorpay Corporate Net Banking",
        "transaction_time": now.strftime("%d %b %Y, %I:%M %p"),
        "previous_balance": "7,450.00",
        "new_balance": "32,450.00",
        "wallet_status": "Active & Operational",
        # Template 5: Invoice generated
        "invoice_number": "INV-2026-0042",
        "invoice_date": now.strftime("%d %b %Y"),
        "due_date": "Paid (Instant Settlement)",
        "tax_id": "27AABCT3521G1Z0",
        "plan_description": "Enterprise Pro Annual Plan (10,000 AI Credits)",
        "subtotal": "21,186.44",
        "tax_amount": "3,813.56 (18% GST)",
        # Template 6: AI search completed
        "query_title": "Precision CNC Machining Suppliers",
        "search_scope": "Global Verified Manufacturers & Exporters",
        "execution_time": "1.42 Seconds",
        "scanned_count": "1,240 Suppliers Scanned",
        "prequalified_count": "14 Pre-Qualified",
        "confidence_score": "98.4% Match Accuracy",
        # Template 7: Password reset
        "user_name": request.user.first_name or "Enterprise User",
        "request_time": now.strftime("%d %b %Y, %I:%M %p"),
        "request_ip": "103.21.144.10 (Mumbai, IN)",
        "token_expiry": "Valid for 24 Hours",
        "reset_url": "https://tradeconnect.com/auth/reset-password/sample-token/",
        # Template 8: Critical alert
        "incident_title": "Vector Pipeline Latency Spike",
        "incident_code": "INC-AI-9021",
        "service_name": "Pinecone / FAISS Embedding Pipeline",
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "severity_level": "CRITICAL (P1)",
        "error_summary": "Upstream timeout exceeded 5000ms threshold on search embeddings.",
        "impacted_operations": "Autonomous discovery query fallback active",
        "retry_status": "Automated failover engaged",
        "environment": "Production Node cluster-01",
        # Template 9: Low balance
        "current_balance": "450.00",
        "threshold_amount": "2,000.00",
        "last_action": "AI Supplier Contact Unlock (-₹50.00)",
        "searches_at_risk": "Direct Contact Unlocks & RFQ Blasts",
        "unlocks_status": "Throttled (Top-up required)",
        "days_remaining": "2 Days",
        # Template 10: Subscription expiring
        "plan_name": "Enterprise Growth Tier",
        "expiry_date": (now + timedelta(days=5)).strftime("%d %b %Y"),
        "billing_cycle": "Annual Subscription",
        "active_inquiries": "24 Open Inquiries",
        "search_quota": "500 AI Searches / Month",
        "badge_status": "Verified Gold Supplier Badge",
        "support_tier": "24/7 Dedicated Support",
        # Template 11: Company registered
        "industry": "Precision Engineering & Manufacturing",
        "registered_at": now.strftime("%d %b %Y, %I:%M %p"),
        "admin_name": "Vikramaditya Roy",
        "contact_email": "vikram@sampleenterprise.com",
        "admin_phone": "+91 98200 11223",
        "location": "Mumbai, Maharashtra, India",
        # Template 12: Export ready
        "report_name": "Q3 Prequalified Supplier Intelligence Dossier",
        "report_format": "Microsoft Excel (.xlsx)",
        "record_count": "142 Records",
        "file_size": "2.8 MB",
        "requested_by": "Procurement Team Admin",
        "generated_at": now.strftime("%d %b %Y, %I:%M %p"),
        "filters_applied": "Location: Western India, MSME: Yes, ISO 9001: Yes",
        "expires_at": (now + timedelta(hours=48)).strftime("%d %b %Y, %I:%M %p"),
        # Standard Destination & Action URLs
        "cta_url": "https://tradeconnect.com/dashboard/",
        "dashboard_url": "https://tradeconnect.com/dashboard/",
        "receipt_url": "https://tradeconnect.com/company/billing/receipts/",
        "invoices_url": "https://tradeconnect.com/company/billing/invoices/",
        "export_url": "https://tradeconnect.com/company/reports/",
        "support_url": "https://tradeconnect.com/help-support/",
        "logs_url": "https://tradeconnect.com/admin-panel/audit-logs/",
        "usage_url": "https://tradeconnect.com/company/billing/",
        "plans_url": "https://tradeconnect.com/company/billing/plans/",
        "companies_url": "https://tradeconnect.com/admin-panel/companies/",
        "reports_url": "https://tradeconnect.com/company/reports/",
    }

    # Custom context if submitted
    custom_vars = request.POST.get("custom_vars")
    if custom_vars:
        try:
            sample_context.update(json.loads(custom_vars))
        except Exception:
            pass

    post_subject = request.POST.get("subject")
    post_body = request.POST.get("html_content")
    post_use_custom = request.POST.get("use_custom_layout") in ("on", "true", "1")
    post_header_html = request.POST.get("header_html")
    post_footer_html = request.POST.get("footer_html")

    target_subject = post_subject if post_subject is not None and post_subject.strip() else template_obj.subject
    target_body = post_body if post_body is not None and post_body.strip() else template_obj.html_content

    subject_rendered = render_string_template(target_subject, sample_context).strip()
    body_rendered = render_string_template(target_body, sample_context)

    # Determine header and footer for preview
    c_header = None
    c_footer = None
    if post_subject is not None:
        if post_use_custom:
            c_header = post_header_html or ""
            c_footer = post_footer_html or ""
    elif template_obj.use_custom_layout:
        c_header = template_obj.header_html
        c_footer = template_obj.footer_html

    if "<html" not in body_rendered.lower():
        full_html = build_branded_html(
            title=subject_rendered,
            summary="Preview rendering of automated transactional notification.",
            cta_url=sample_context.get("cta_url"),
            cta_text="View in Platform",
            severity="INFO",
            custom_content=body_rendered,
            event_key=template_obj.event_key,
            template_obj=template_obj,
            custom_header_html=c_header,
            custom_footer_html=c_footer,
        )
    else:
        full_html = body_rendered

    return JsonResponse({
        "status": "success",
        "subject": subject_rendered,
        "html": full_html,
    })


@login_required
@superadmin_required
@require_POST
def admin_email_template_test_send_view(request, pk):
    """
    Sends a test email of the specified template to an admin-designated address.
    """
    template_obj = get_object_or_404(NotificationTemplate, pk=pk)
    target_email = request.POST.get("target_email", "").strip() or request.user.email

    from apps.notifications.services.email_engine import trigger_email_notification
    logs = trigger_email_notification(
        event_key=template_obj.event_key,
        context={
            "recipient_name": request.user.get_full_name() or "Admin Tester",
            "requirement_title": "Test Sourcing Item",
            "product_title": "Test Catalog Product",
            "category": "Test Category",
            "quantity": "100 Units",
            "count": "5",
            "query_title": "Test Query",
            "inquiry_id": "999",
            "supplier_name": "Test Supplier",
            "buyer_company": "Test Buyer Corp",
            "amount": "5,000",
            "transaction_ref": "TEST-RECH-123",
            "new_balance": "10,000",
            "current_balance": "250",
            "invoice_number": "TEST-INV-001",
            "user_name": request.user.first_name,
            "cta_url": request.build_absolute_uri("/"),
        },
        user=request.user,
        async_dispatch=False,
    )

    if logs and any(l.status == NotificationLog.Status.SENT for l in logs):
        messages.success(request, f"Test email for '{template_obj.name}' dispatched to {target_email}!")
    else:
        last_err = logs[0].last_error if logs else "No active rule matched"
        messages.warning(request, f"Test dispatch initiated (Status: {logs[0].status if logs else 'None'}). Result / Log: {last_err}")

    return redirect("admin-panel-email-templates")


@login_required
@superadmin_required
def admin_email_triggers_view(request):
    """
    Super Admin Centralized Notification Management:
    Lists all event triggers with Category tabs (User, Company Admin, Super Admin),
    Delivery Mode filters (Automatic, Manual, Both), search, active toggle,
    full configuration modal, preview modal, manual send modal, test send modal.
    """
    rules_qs = NotificationRule.objects.select_related("template").order_by("target_audience", "name")

    # Counts for Audience Category tabs
    total_count = NotificationRule.objects.count()
    user_count = NotificationRule.objects.filter(target_audience=NotificationRule.TargetAudience.USER).count()
    company_admin_count = NotificationRule.objects.filter(target_audience=NotificationRule.TargetAudience.COMPANY_ADMIN).count()
    super_admin_count = NotificationRule.objects.filter(target_audience=NotificationRule.TargetAudience.SUPER_ADMIN).count()

    # Category / Target Audience filter
    audience_filter = request.GET.get("audience", "").strip().upper()
    if audience_filter in ["USER", "COMPANY_ADMIN", "SUPER_ADMIN"]:
        rules_qs = rules_qs.filter(target_audience=audience_filter)

    # Delivery Mode filter
    mode_filter = request.GET.get("mode", "").strip().upper()
    if mode_filter in ["AUTOMATIC", "MANUAL", "BOTH"]:
        rules_qs = rules_qs.filter(delivery_mode=mode_filter)

    # Search query
    q = request.GET.get("q", "").strip()
    if q:
        rules_qs = rules_qs.filter(
            Q(name__icontains=q)
            | Q(event_key__icontains=q)
            | Q(description__icontains=q)
            | Q(template__subject__icontains=q)
        )

    # Status filter
    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter == "enabled":
        rules_qs = rules_qs.filter(is_enabled=True)
    elif status_filter == "disabled":
        rules_qs = rules_qs.filter(is_enabled=False)

    page_obj, query_string = paginate_and_preserve(request, rules_qs, per_page=12)

    # Templates and choices for configuration modal
    all_templates = NotificationTemplate.objects.filter(is_active=True).order_by("name")
    recipient_types = NotificationRule.RecipientType.choices
    delivery_modes = NotificationRule.DeliveryMode.choices
    target_audiences = NotificationRule.TargetAudience.choices
    severities = NotificationRule.Severity.choices
    priorities = NotificationRule.Priority.choices

    # Sample companies and users for manual send modal
    from apps.companies.models import Company
    from django.contrib.auth import get_user_model
    User = get_user_model()
    companies = Company.objects.filter(is_deleted=False).order_by("name")[:30]
    sample_users = User.objects.filter(is_active=True).select_related("company").order_by("first_name", "last_name")[:30]

    context = {
        "page_title": "Centralized Notification Management",
        "active_tab": "triggers",
        "rules": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "selected_audience": audience_filter,
        "selected_mode": mode_filter,
        "selected_status": status_filter,
        "search_query": q,
        "total_count": total_count,
        "user_count": user_count,
        "company_admin_count": company_admin_count,
        "super_admin_count": super_admin_count,
        "all_templates": all_templates,
        "recipient_types": recipient_types,
        "delivery_modes": delivery_modes,
        "target_audiences": target_audiences,
        "channels": NotificationRule.Channel.choices,
        "severities": severities,
        "priorities": priorities,
        "companies": companies,
        "sample_users": sample_users,
    }
    return render(request, "admin_panel/email/triggers.html", context)


@login_required
@superadmin_required
@require_POST
def admin_email_trigger_toggle_view(request, pk):
    """
    POST action to toggle an event trigger rule on or off.
    Enforces business security rule: Mandatory authentication emails cannot be disabled.
    Supports both traditional POST redirect and AJAX/JSON API responses.
    """
    rule_obj = get_object_or_404(NotificationRule, pk=pk)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    if rule_obj.is_mandatory_auth and rule_obj.is_enabled:
        msg = f"Security Alert: '{rule_obj.name}' is a mandatory authentication notification and cannot be disabled."
        if is_ajax:
            return JsonResponse({"status": "error", "message": msg}, status=400)
        messages.error(request, msg)
        return redirect("admin-panel-email-triggers")

    rule_obj.is_enabled = not rule_obj.is_enabled
    rule_obj.save(update_fields=["is_enabled"])

    status_str = "Enabled" if rule_obj.is_enabled else "Disabled"
    msg = f"Notification '{rule_obj.name}' is now {status_str}."
    if is_ajax:
        return JsonResponse({"status": "success", "is_enabled": rule_obj.is_enabled, "message": msg})
    messages.success(request, msg)
    return redirect("admin-panel-email-triggers")


@login_required
@superadmin_required
@require_POST
def admin_email_trigger_edit_view(request, pk):
    """
    Saves full notification rule configuration from modal.
    """
    from decimal import Decimal
    rule = get_object_or_404(NotificationRule, pk=pk)

    rule.name = request.POST.get("name", rule.name).strip()
    rule.description = request.POST.get("description", rule.description).strip()
    
    deliv_mode = request.POST.get("delivery_mode", rule.delivery_mode)
    if deliv_mode in dict(NotificationRule.DeliveryMode.choices):
        rule.delivery_mode = deliv_mode

    chan = request.POST.get("channel", rule.channel)
    if chan in dict(NotificationRule.Channel.choices):
        rule.channel = chan

    aud = request.POST.get("target_audience", rule.target_audience)
    if aud in dict(NotificationRule.TargetAudience.choices):
        rule.target_audience = aud

    tpl_id = request.POST.get("template_id")
    if tpl_id:
        tpl = NotificationTemplate.objects.filter(id=tpl_id).first()
        if tpl:
            rule.template = tpl

    rec_type = request.POST.get("recipient_type", rule.recipient_type)
    if rec_type in dict(NotificationRule.RecipientType.choices):
        rule.recipient_type = rec_type

    rule.custom_recipients = request.POST.get("custom_recipients", "").strip()
    rule.subject_override = request.POST.get("subject_override", "").strip()
    rule.cc_recipients = request.POST.get("cc_recipients", "").strip()
    rule.bcc_recipients = request.POST.get("bcc_recipients", "").strip()

    thresh = request.POST.get("threshold_value", "").strip()
    if thresh:
        try:
            rule.threshold_value = Decimal(thresh)
        except Exception:
            pass
    else:
        rule.threshold_value = None

    cooldown = request.POST.get("cooldown_hours", "").strip()
    if cooldown and cooldown.isdigit():
        rule.cooldown_hours = max(1, int(cooldown))

    remind_days = request.POST.get("reminder_days_before", "").strip()
    if remind_days and remind_days.isdigit():
        rule.reminder_days_before = max(1, int(remind_days))

    sev = request.POST.get("severity", rule.severity)
    if sev in dict(NotificationRule.Severity.choices):
        rule.severity = sev

    pri = request.POST.get("priority", rule.priority)
    if pri in dict(NotificationRule.Priority.choices):
        rule.priority = pri

    is_en = request.POST.get("is_enabled") in ["on", "true", "1"]
    if rule.is_mandatory_auth:
        rule.is_enabled = True
    else:
        rule.is_enabled = is_en

    rule.allow_manual_send = request.POST.get("allow_manual_send") in ["on", "true", "1"]
    rule.save()

    messages.success(request, f"Configuration for '{rule.name}' saved successfully.")
    return redirect("admin-panel-email-triggers")


@login_required
@superadmin_required
@require_POST
def admin_email_trigger_manual_send_view(request, pk):
    """
    Manually dispatches an authorized notification (for MANUAL or BOTH mode).
    Validates recipient permissions, logs audit entry with sent_by, and delivers email.
    """
    rule = get_object_or_404(NotificationRule, pk=pk)

    if rule.delivery_mode == NotificationRule.DeliveryMode.AUTOMATIC:
        messages.error(request, f"'{rule.name}' is configured for Automatic delivery only. Manual dispatch is disabled.")
        return redirect("admin-panel-email-triggers")

    target_email = request.POST.get("target_email", "").strip()
    target_company_id = request.POST.get("target_company_id", "").strip()
    custom_subject = request.POST.get("override_subject", "").strip()
    reason_note = request.POST.get("reason_note", "").strip()

    from apps.companies.models import Company
    company = None
    if target_company_id:
        company = Company.objects.filter(id=target_company_id).first()

    from apps.notifications.services.email_engine import trigger_email_notification
    custom_emails = [target_email] if target_email else None

    logs = trigger_email_notification(
        event_key=rule.event_key,
        company=company,
        user=None,
        is_manual=True,
        sent_by=request.user,
        custom_recipient_emails=custom_emails,
        override_subject=custom_subject if custom_subject else None,
        context={
            "manual_sender": request.user.get_full_name() or request.user.email,
            "manual_reason": reason_note,
            "cta_url": request.build_absolute_uri("/dashboard/"),
        },
        async_dispatch=False,
    )

    if logs:
        sent_ok = any(l.status == NotificationLog.Status.SENT for l in logs)
        if sent_ok:
            rec_str = ", ".join(l.recipient_email for l in logs)
            messages.success(request, f"Manual dispatch for '{rule.name}' succeeded! Dispatched to: {rec_str}.")
        else:
            messages.warning(request, f"Manual dispatch initiated ({len(logs)} log entries created). Last status: {logs[0].status}.")
    else:
        messages.error(request, f"Manual dispatch could not be sent. Check recipient criteria or delivery mode.")

    return redirect("admin-panel-email-triggers")


@login_required
@superadmin_required
def admin_email_trigger_preview_api(request, pk):
    """
    JSON preview endpoint for a Notification Rule.
    Renders subject and full HTML layout with contextual sample data.
    """
    rule = get_object_or_404(NotificationRule, pk=pk)
    tpl = rule.template

    now = timezone.now()
    sample_context = {
        "recipient_name": request.user.get_full_name() or "Admin User",
        "recipient_email": request.user.email,
        "company_name": "Sample Enterprise Ltd.",
        "user_name": request.user.first_name or "Enterprise User",
        "role_name": "Procurement Manager",
        "role": "Procurement Specialist",
        "status_state": "Active",
        "action_date": now.strftime("%d %b %Y, %I:%M %p"),
        "operator_name": request.user.get_full_name() or "Super Administrator",
        "reason": "Administrative account update",
        "decision_status": "Approved",
        "decision": "Approved",
        "verified_badge": "Active (Verified)",
        "admin_operator": request.user.get_full_name() or "Platform Administrator",
        "decision_date": now.strftime("%d %b %Y"),
        "new_user_name": "Priya Patel",
        "new_user_email": "priya.patel@enterprise.com",
        "plan_name": "Enterprise Pro Growth",
        "expiry_date": (now + timedelta(days=7)).strftime("%d %b %Y"),
        "days_left": "7",
        "renewal_amount": "15,000.00",
        "auto_renew_status": "Active (Prepaid Auto-Pay)",
        "amount": "15,000.00",
        "current_credits": "35",
        "threshold_credits": str(rule.threshold_value or 50),
        "failure_reason": "Card expired / insufficient funds",
        "invoice_id": "INV-2026-0098",
        "gateway_ref": "GW-FAIL-9821",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_message": "Automated renewal failed due to insufficient wallet funds",
        "failed_event": rule.event_key,
        "attempts_count": "3",
        "log_id": "8412",
        "alert_title": "Repeated Authentication Failures",
        "incident_code": "SEC-WARN-091",
        "source_ip": "103.21.144.10",
        "incident_time": now.strftime("%d %b %Y, %I:%M %p"),
        "verification_url": "https://tradeconnect.com/auth/verify-email/sample-token/",
        "reset_url": "https://tradeconnect.com/auth/reset-password/sample-token/",
        "cta_url": "https://tradeconnect.com/dashboard/",
        "topup_url": "https://tradeconnect.com/company/billing/",
        "review_url": "https://tradeconnect.com/admin-panel/companies/",
        "audit_url": "https://tradeconnect.com/admin-panel/audit-logs/",
        "dashboard_url": "https://tradeconnect.com/dashboard/",
        "current_year": now.year,
        "platform_name": "AI Procurement & Sales Platform",
    }

    subj_raw = rule.subject_override or tpl.subject
    subject_rendered = render_string_template(subj_raw, sample_context).strip()
    body_rendered = render_string_template(tpl.html_content, sample_context)

    if "<html" not in body_rendered.lower():
        full_html = build_branded_html(
            title=subject_rendered,
            summary="Operational preview of rendered notification rule.",
            cta_url=sample_context.get("cta_url"),
            cta_text="View in Platform",
            severity=rule.severity,
            custom_content=body_rendered,
            event_key=rule.event_key,
            template_obj=tpl,
        )
    else:
        full_html = body_rendered

    return JsonResponse({
        "status": "success",
        "name": rule.name,
        "event_key": rule.event_key,
        "delivery_mode": rule.delivery_mode,
        "subject": subject_rendered,
        "html": full_html,
    })


@login_required
@superadmin_required
@require_POST
def admin_email_trigger_test_send_api(request, pk):
    """
    Sends an immediate live test email for this notification rule to the logged-in admin.
    """
    rule = get_object_or_404(NotificationRule, pk=pk)
    target_email = request.POST.get("target_email", "").strip() or request.user.email

    from apps.notifications.services.email_engine import trigger_email_notification
    logs = trigger_email_notification(
        event_key=rule.event_key,
        company=None,
        user=request.user,
        is_manual=True,
        sent_by=request.user,
        custom_recipient_emails=[target_email],
        context={
            "user_name": request.user.get_full_name() or "Admin Tester",
            "recipient_name": request.user.get_full_name() or "Admin Tester",
            "company_name": "TradeConnect Test Workspace",
            "cta_url": request.build_absolute_uri("/dashboard/"),
        },
        async_dispatch=False,
    )

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    if logs and any(l.status == NotificationLog.Status.SENT for l in logs):
        msg = f"Test email for '{rule.name}' successfully sent to {target_email}!"
        if is_ajax:
            return JsonResponse({"status": "success", "message": msg})
        messages.success(request, msg)
    else:
        err = logs[0].last_error if logs else "No dispatch log created"
        msg = f"Test email initiated for {target_email}. Status: {logs[0].status if logs else 'None'}. Info: {err}"
        if is_ajax:
            return JsonResponse({"status": "warning", "message": msg})
        messages.warning(request, msg)

    return redirect("admin-panel-email-triggers")


@login_required
@superadmin_required
def admin_email_smtp_view(request):
    """
    Super Admin SMTP & Provider Settings:
    Configure custom or environment SMTP credentials with masking and live ping testing.
    """
    active_smtp = SMTPSetting.get_active_config()

    if request.method == "POST":
        backend_type = request.POST.get("backend_type", SMTPSetting.BackendType.SMTP)
        sender_name = request.POST.get("sender_name", "").strip() or "AI Procurement & Sales Platform"
        sender_email = request.POST.get("sender_email", "").strip() or "notifications@procurement-ai.com"
        host = request.POST.get("host", "").strip() or "smtp.gmail.com"
        port_str = request.POST.get("port", "587").strip()
        use_tls = request.POST.get("use_tls") == "on"
        use_ssl = request.POST.get("use_ssl") == "on"
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        is_active = request.POST.get("is_active") == "on"

        try:
            port = int(port_str)
        except ValueError:
            port = 587

        if not active_smtp:
            active_smtp = SMTPSetting()

        active_smtp.backend_type = backend_type
        active_smtp.sender_name = sender_name
        active_smtp.sender_email = sender_email
        active_smtp.host = host
        active_smtp.port = port
        active_smtp.use_tls = use_tls
        active_smtp.use_ssl = use_ssl
        active_smtp.username = username
        if password:
            active_smtp.set_password(password)
        active_smtp.is_active = is_active
        active_smtp.updated_by = request.user
        active_smtp.save()

        # If this one is active, deactivate other SMTP settings
        if is_active:
            SMTPSetting.objects.exclude(pk=active_smtp.pk).update(is_active=False)

        messages.success(request, "SMTP settings successfully saved.")
        return redirect("admin-panel-email-smtp")

    context = {
        "page_title": "SMTP Provider Configuration",
        "active_tab": "smtp",
        "smtp": active_smtp,
    }
    return render(request, "admin_panel/email/smtp.html", context)


@login_required
@superadmin_required
@require_POST
def admin_email_smtp_test_view(request):
    """
    POST action to test SMTP transmission with an active diagnostic ping.
    """
    target_email = request.POST.get("target_email", "").strip() or request.user.email

    success, message = test_smtp_connection(target_email)
    if success:
        messages.success(request, f"SMTP Connection Test Passed! Diagnostic ping received by {target_email}.")
    else:
        messages.error(request, f"SMTP Connection Test Failed: {message}")

    return redirect("admin-panel-email-smtp")


@login_required
@superadmin_required
def admin_email_logs_view(request):
    """
    Super Admin Email Delivery Outbox Logs:
    Searchable, filterable audit trail of all email transmissions with delivery attempts.
    """
    status_filter = request.GET.get("status", "").strip().upper()
    severity_filter = request.GET.get("severity", "").strip().upper()
    search_query = request.GET.get("q", "").strip()
    from_date = request.GET.get("from_date", "").strip()
    to_date = request.GET.get("to_date", "").strip()

    logs_qs = NotificationLog.objects.select_related("company", "user", "template").order_by("-created_at")

    if status_filter:
        logs_qs = logs_qs.filter(status=status_filter)
    if severity_filter:
        logs_qs = logs_qs.filter(severity=severity_filter)
    if search_query:
        logs_qs = logs_qs.filter(
            Q(recipient_email__icontains=search_query)
            | Q(subject__icontains=search_query)
            | Q(event_key__icontains=search_query)
            | Q(company__name__icontains=search_query)
        )
    if from_date:
        logs_qs = logs_qs.filter(created_at__date__gte=from_date)
    if to_date:
        logs_qs = logs_qs.filter(created_at__date__lte=to_date)

    # CSV Export Handler
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        today_str = timezone.now().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="Email_Delivery_Logs_{today_str}.csv"'
        response.write("\ufeff")  # UTF-8 BOM for Excel
        writer = csv.writer(response)
        writer.writerow(["Log ID", "Event Key", "Recipient Email", "Recipient Name", "Subject", "Company", "Status", "Severity", "Attempts", "Dispatched At"])
        for lg in logs_qs:
            c_name = lg.company.name if lg.company else ""
            writer.writerow([lg.id, lg.event_key, lg.recipient_email, lg.recipient_name or "", lg.subject, c_name, lg.get_status_display(), lg.get_severity_display(), lg.attempt_count, lg.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        return response

    statuses = NotificationLog.Status.choices
    severities = NotificationLog.Severity.choices
    page_obj, query_string = paginate_and_preserve(request, logs_qs, per_page=10)

    context = {
        "page_title": "Email Delivery Audit Logs",
        "active_tab": "logs",
        "logs": page_obj,
        "page_obj": page_obj,
        "query_string": query_string,
        "statuses": statuses,
        "severities": severities,
        "selected_status": status_filter,
        "selected_severity": severity_filter,
        "search_query": search_query,
        "from_date": from_date,
        "to_date": to_date,
    }
    return render(request, "admin_panel/email/logs.html", context)


@login_required
@superadmin_required
def admin_email_log_detail_api(request, pk):
    """
    JSON endpoint providing complete payload, rendered HTML, and attempts history for a log entry.
    """
    log_obj = get_object_or_404(NotificationLog, pk=pk)
    attempts = [
        {
            "attempt_number": a.attempt_number,
            "status": a.status,
            "error_message": a.error_message,
            "timestamp": a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for a in log_obj.attempts.all().order_by("attempt_number")
    ]

    return JsonResponse({
        "id": log_obj.id,
        "event_key": log_obj.event_key,
        "recipient_email": log_obj.recipient_email,
        "recipient_name": log_obj.recipient_name,
        "subject": log_obj.subject,
        "status": log_obj.status,
        "severity": log_obj.severity,
        "attempts_count": log_obj.attempts_count,
        "max_attempts": log_obj.max_attempts,
        "last_error": log_obj.last_error,
        "html_body": log_obj.html_body,
        "context_data": log_obj.context_data,
        "created_at": log_obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "sent_at": log_obj.sent_at.strftime("%Y-%m-%d %H:%M:%S") if log_obj.sent_at else None,
        "attempts": attempts,
    })


@login_required
@superadmin_required
@require_POST
def admin_email_log_retry_view(request, pk):
    """
    POST action to manually retry a failed or queued email transmission.
    """
    log_obj = get_object_or_404(NotificationLog, pk=pk)
    success = retry_failed_notification_log(log_obj, async_dispatch=False)

    if success:
        messages.success(request, f"Log #{log_obj.id} ({log_obj.recipient_email}) successfully delivered on retry!")
    else:
        messages.error(request, f"Retry failed for Log #{log_obj.id}: {log_obj.last_error}")

    return redirect("admin-panel-email-logs")


@login_required
@superadmin_required
@require_POST
def admin_email_logs_retry_all_view(request):
    """
    Super Admin bulk action to retry all failed email logs from the last 24-48 hours.
    Resets status to QUEUED and increments max_attempts for asynchronous worker retry.
    """
    hours = int(request.POST.get("hours", 24))
    cutoff = timezone.now() - timedelta(hours=hours)

    failed_logs = NotificationLog.objects.filter(
        status=NotificationLog.Status.FAILED,
        created_at__gte=cutoff,
    )
    count = 0
    for log in failed_logs:
        retry_failed_notification_log(log, async_dispatch=True)
        count += 1

    messages.success(request, f"Re-queued {count} failed email(s) from the last {hours}h for automated background delivery.")
    return redirect("admin-panel-email-logs")


@login_required
@superadmin_required
def admin_email_alerts_view(request):
    """
    Super Admin Platform Alerts:
    Displays high-severity/critical alerts, delivery retry exhausted events, and system notifications.
    """
    alert_logs = NotificationLog.objects.filter(
        severity__in=[NotificationLog.Severity.WARNING, NotificationLog.Severity.CRITICAL]
    ).select_related("company", "user").order_by("-created_at")[:50]

    context = {
        "page_title": "Platform Alert Policies & Log",
        "active_tab": "alerts",
        "alert_logs": alert_logs,
    }
    return render(request, "admin_panel/email/alerts.html", context)


@login_required
@superadmin_required
def admin_push_notifications_overview_view(request):
    """
    Super Admin Web Push Notification Telemetry & Monitoring:
    Inspect active devices, browser breakdown, delivery logs, failure reasons, and test push.
    """
    active_devices_count = DeviceToken.objects.filter(is_active=True).count()
    device_breakdown = (
        DeviceToken.objects.filter(is_active=True)
        .values("device_type")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    total_push_logs = PushDeliveryLog.objects.count()
    success_push_logs = PushDeliveryLog.objects.filter(status=PushDeliveryLog.Status.SUCCESS).count()
    failed_push_logs = PushDeliveryLog.objects.filter(status=PushDeliveryLog.Status.FAILED).count()
    success_rate = (
        round((success_push_logs / total_push_logs) * 100, 1)
        if total_push_logs > 0
        else 100.0
    )

    logs_qs = PushDeliveryLog.objects.select_related("user", "company", "device").order_by("-created_at")

    # Status filter
    status_filter = request.GET.get("status", "").strip().upper()
    if status_filter in [PushDeliveryLog.Status.SUCCESS, PushDeliveryLog.Status.FAILED]:
        logs_qs = logs_qs.filter(status=status_filter)

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        logs_qs = logs_qs.filter(
            Q(user__email__icontains=q)
            | Q(event_key__icontains=q)
            | Q(error_message__icontains=q)
        )

    page_obj, pagination_context = paginate_and_preserve(request, logs_qs, per_page=15)

    context = {
        "page_title": "Web Push Telemetry & Monitoring",
        "active_tab": "push_telemetry",
        "active_devices_count": active_devices_count,
        "device_breakdown": device_breakdown,
        "total_push_logs": total_push_logs,
        "success_push_logs": success_push_logs,
        "failed_push_logs": failed_push_logs,
        "success_rate": success_rate,
        "page_obj": page_obj,
        "logs": page_obj.object_list,
        "pagination": pagination_context,
        "status_filter": status_filter,
        "q": q,
    }
    return render(request, "admin_panel/notifications/push_overview.html", context)

