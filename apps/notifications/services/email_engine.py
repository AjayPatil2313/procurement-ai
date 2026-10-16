import logging
import os
import threading
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Template, Context
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape

from apps.notifications.models import (
    NotificationTemplate,
    NotificationRule,
    NotificationLog,
    EmailDeliveryAttempt,
    EmailLayoutSetting,
)
from apps.notifications.services.smtp_service import (
    get_active_email_connection,
    get_default_from_email,
)
from apps.notifications.services.recipient_resolver import resolve_rule_recipients

logger = logging.getLogger(__name__)

EVENT_BANNER_CONFIGS = {
    # 1. New Inquiry Received
    "seller.inquiry_received": {
        "theme": "blue",
        "icon": "&#9993;",  # Envelope
        "title": "New Inquiry",
        "highlight": "Received!",
        "subtitle": "You have received a new buyer inquiry for your product. Here are the details:",
        "cta_text": "View Inquiry",
        "secondary_cta_text": "Go to Vendor Dashboard",
    },
    # 2. Matching Suppliers Found
    "buyer.matching_suppliers_found": {
        "theme": "blue",
        "icon": "&#128269;",  # Search
        "title": "Matching Suppliers",
        "highlight": "Found!",
        "subtitle": "Our autonomous AI engine has discovered verified suppliers matching your requirements:",
        "cta_text": "Review Suppliers & Send RFQ",
        "secondary_cta_text": "Go to Procurement Dashboard",
    },
    "buyer.search_completed": {
        "theme": "blue",
        "icon": "&#128269;",
        "title": "Supplier Search",
        "highlight": "Completed!",
        "subtitle": "Our autonomous AI engine has discovered verified suppliers matching your requirements:",
        "cta_text": "Review Suppliers & Send RFQ",
        "secondary_cta_text": "Go to Procurement Dashboard",
    },
    # 3. New Buyer Lead
    "seller.new_buyer_lead": {
        "theme": "purple",
        "icon": "&#127919;",  # Bullseye / Target
        "title": "New Buyer Lead",
        "highlight": "Discovered!",
        "subtitle": "A new high-intent buyer requirement matching your catalog offerings has just been published:",
        "cta_text": "Pitch & Submit Quote",
        "secondary_cta_text": "View All Buyer Leads",
    },
    "seller.search_completed": {
        "theme": "purple",
        "icon": "&#127919;",
        "title": "Buyer Leads Discovery",
        "highlight": "Completed!",
        "subtitle": "Your search for prospective purchasing organizations is complete:",
        "cta_text": "View Buyer Leads",
        "secondary_cta_text": "Go to Vendor Dashboard",
    },
    # 4. Wallet Recharge Successful
    "billing.wallet_recharge_success": {
        "theme": "emerald",
        "icon": "&#10004;",  # Checkmark
        "title": "Wallet Recharge",
        "highlight": "Successful!",
        "subtitle": "Your company wallet has been credited and is ready for immediate platform use:",
        "cta_text": "View Wallet Transactions",
        "secondary_cta_text": "Download Receipt",
    },
    "billing.wallet_recharged": {
        "theme": "emerald",
        "icon": "&#10004;",
        "title": "Wallet Recharge",
        "highlight": "Successful!",
        "subtitle": "Your company wallet has been credited and is ready for immediate platform use:",
        "cta_text": "View Wallet Transactions",
        "secondary_cta_text": "Download Receipt",
    },
    # 5. Invoice Generated
    "billing.invoice_generated": {
        "theme": "teal",
        "icon": "&#128196;",  # Document / Invoice
        "title": "Tax Invoice",
        "highlight": "Generated!",
        "subtitle": "An official tax invoice has been generated for your recent transaction:",
        "cta_text": "Download PDF Invoice",
        "secondary_cta_text": "View All Invoices",
    },
    # 6. AI Search Completed
    "search.ai_completed": {
        "theme": "purple",
        "icon": "&#10024;",  # Sparkles / AI
        "title": "AI Search",
        "highlight": "Completed!",
        "subtitle": "Autonomous intelligence search finished scanning enterprise catalogs and registries:",
        "cta_text": "Explore Search Results",
        "secondary_cta_text": "Export Results CSV",
    },
    # 7. Password Reset
    "auth.password_reset": {
        "theme": "blue",
        "icon": "&#128274;",  # Lock
        "title": "Password Reset",
        "highlight": "Request!",
        "subtitle": "We received a security request to reset your TradeConnect enterprise password:",
        "cta_text": "Reset My Password",
        "secondary_cta_text": "Contact Security Support",
    },
    # 8. Critical System Alert
    "admin.critical_system_alert": {
        "theme": "rose",
        "icon": "&#9888;",  # Alert
        "title": "Critical System",
        "highlight": "Alert!",
        "subtitle": "Immediate Super Admin intervention required. Critical platform event detected:",
        "cta_text": "Open Super Admin Console",
        "secondary_cta_text": "Inspect System Logs",
    },
    "admin.company_suspended": {
        "theme": "rose",
        "icon": "&#9888;",  # Warning
        "title": "Corporate Account",
        "highlight": "Suspended!",
        "subtitle": "Security & Governance notice regarding the administrative action on corporate workspace:",
        "cta_text": "Open Super Admin Console",
        "secondary_cta_text": "Inspect Companies Desk",
    },
    "company.suspended": {
        "theme": "rose",
        "icon": "&#9888;",  # Warning
        "title": "Corporate Account",
        "highlight": "Suspended!",
        "subtitle": "Platform governance notice regarding the immediate suspension of your corporate organization:",
        "cta_text": "Contact Support Desk",
        "secondary_cta_text": "Platform Helpdesk",
    },
    "company.reactivated": {
        "theme": "emerald",
        "icon": "&#10004;",  # Checkmark
        "title": "Corporate Account",
        "highlight": "Reactivated!",
        "subtitle": "Your corporate account has been reactivated and full platform access is restored:",
        "cta_text": "Go to Company Dashboard",
        "secondary_cta_text": "Manage Workspace",
    },
    "admin.ai_provider_error": {
        "theme": "rose",
        "icon": "&#9888;",
        "title": "AI Provider",
        "highlight": "Failure!",
        "subtitle": "Autonomous AI provider failure detected. Fallback mechanisms active:",
        "cta_text": "Inspect AI Providers Desk",
        "secondary_cta_text": "View Error Logs",
    },
    # 9. Low Wallet Balance
    "billing.low_wallet_balance": {
        "theme": "amber",
        "icon": "&#9888;",  # Warning
        "title": "Low Wallet Balance",
        "highlight": "Warning!",
        "subtitle": "Your company wallet has fallen below the operating threshold. Please top up to maintain services:",
        "cta_text": "Recharge Wallet Now",
        "secondary_cta_text": "View Usage Breakdown",
    },
    "billing.low_balance": {
        "theme": "amber",
        "icon": "&#9888;",
        "title": "Low Wallet Balance",
        "highlight": "Warning!",
        "subtitle": "Your company wallet has fallen below the operating threshold. Please top up to maintain services:",
        "cta_text": "Recharge Wallet Now",
        "secondary_cta_text": "View Usage Breakdown",
    },
    # 10. Subscription Expiring
    "billing.subscription_expiring": {
        "theme": "amber",
        "icon": "&#128197;",  # Calendar
        "title": "Subscription Expiring",
        "highlight": "Soon!",
        "subtitle": "Your enterprise subscription plan is scheduled to expire. Renew to maintain uninterrupted services:",
        "cta_text": "Renew Subscription Now",
        "secondary_cta_text": "Compare Plans",
    },
    # 11. New Company Registration
    "admin.company_registered": {
        "theme": "blue",
        "icon": "&#127970;",  # Building
        "title": "New Company",
        "highlight": "Registered!",
        "subtitle": "A new enterprise organization has registered on the platform and awaits review:",
        "cta_text": "Review & Verify Company",
        "secondary_cta_text": "View All Companies",
    },
    "admin.new_company": {
        "theme": "blue",
        "icon": "&#127970;",
        "title": "New Company",
        "highlight": "Registered!",
        "subtitle": "A new enterprise organization has registered on the platform and awaits review:",
        "cta_text": "Review & Verify Company",
        "secondary_cta_text": "View All Companies",
    },
    # 12. Export Report Ready
    "reports.export_ready": {
        "theme": "teal",
        "icon": "&#128229;",  # Download
        "title": "Export Report",
        "highlight": "Ready!",
        "subtitle": "Your requested data export has been compiled and is ready for secure download:",
        "cta_text": "Download Report Now",
        "secondary_cta_text": "Go to Reports Desk",
    },
    # 13. Registration & Welcome
    "auth.welcome": {
        "theme": "blue",
        "icon": "&#128100;",
        "title": "Welcome to TradeConnect",
        "highlight": "Welcome!",
        "subtitle": "Your account has been created and is ready for immediate deployment:",
        "cta_text": "Explore Your Dashboard",
        "secondary_cta_text": "Complete Profile",
    },
    "auth.welcome_registration": {
        "theme": "blue",
        "icon": "&#128100;",
        "title": "Welcome to TradeConnect",
        "highlight": "Welcome!",
        "subtitle": "Your account has been created and is ready for immediate deployment:",
        "cta_text": "Explore Your Dashboard",
        "secondary_cta_text": "Complete Profile",
    },
    # 14. Email Verification
    "auth.email_verification": {
        "theme": "blue",
        "icon": "&#9993;",
        "title": "Email Verification",
        "highlight": "Verify Email!",
        "subtitle": "Please confirm your email address to authenticate your enterprise account:",
        "cta_text": "Verify Email Address",
        "secondary_cta_text": "Help Center",
    },
    # 15. Password Changed
    "auth.password_changed": {
        "theme": "emerald",
        "icon": "&#128272;",
        "title": "Password Updated",
        "highlight": "Confirmed!",
        "subtitle": "The password for your TradeConnect account was modified successfully:",
        "cta_text": "Review Account Security",
        "secondary_cta_text": "Contact Support",
    },
    # 16. Account Activation / Deactivation
    "user.activation_deactivation": {
        "theme": "blue",
        "icon": "&#128100;",
        "title": "Account Status",
        "highlight": "Updated!",
        "subtitle": "Your team member workspace access state has been modified:",
        "cta_text": "Access Workspace",
        "secondary_cta_text": "Contact Administrator",
    },
    # 17. Subscription Expiry Reminder (User)
    "billing.subscription_expiry_user": {
        "theme": "amber",
        "icon": "&#128337;",
        "title": "Subscription Expiry",
        "highlight": "Notice!",
        "subtitle": "Your company subscription is approaching expiration. Inform your billing administrator:",
        "cta_text": "View Plan Status",
        "secondary_cta_text": "Billing Details",
    },
    # 18. New Company User Added
    "company.user_added": {
        "theme": "blue",
        "icon": "&#128101;",
        "title": "New Team Member",
        "highlight": "Added!",
        "subtitle": "A new team member has joined your corporate workspace on TradeConnect:",
        "cta_text": "Manage Team Members",
        "secondary_cta_text": "View Company Settings",
    },
    # 19. Company Verification Decision
    "company.verification_decision": {
        "theme": "emerald",
        "icon": "&#128737;",
        "title": "Corporate Verification",
        "highlight": "Decision!",
        "subtitle": "Platform administrators have reviewed your company verification credentials:",
        "cta_text": "View Verification Status",
        "secondary_cta_text": "Company Profile",
    },
    # 20. Credits Low
    "billing.credits_low": {
        "theme": "amber",
        "icon": "&#128267;",
        "title": "Low AI Credits",
        "highlight": "Alert!",
        "subtitle": "Your available AI search and discovery credits have crossed the low threshold:",
        "cta_text": "Recharge Credits Now",
        "secondary_cta_text": "View Credit Ledger",
    },
    # 21. Subscription Expiring (Admin)
    "billing.subscription_expiring_admin": {
        "theme": "amber",
        "icon": "&#128197;",
        "title": "Subscription Renewal",
        "highlight": "Upcoming!",
        "subtitle": "Your enterprise subscription plan is approaching renewal or expiration:",
        "cta_text": "Manage Subscription & Billing",
        "secondary_cta_text": "View Plans",
    },
    # 22. Payment Failed
    "billing.payment_failed": {
        "theme": "rose",
        "icon": "&#9888;",
        "title": "Payment Failed",
        "highlight": "Notice!",
        "subtitle": "A recent subscription payment or wallet recharge attempt could not be processed:",
        "cta_text": "Retry Payment Now",
        "secondary_cta_text": "Recharge Wallet",
    },
    # 23. Company Profile Updated
    "company.profile_updated": {
        "theme": "blue",
        "icon": "&#128221;",
        "title": "Profile Updated",
        "highlight": "Notice!",
        "subtitle": "Critical corporate information or tax credentials were recently updated:",
        "cta_text": "Review Company Profile",
        "secondary_cta_text": "Team Management",
    },
    # 24. Super Admin: Verification Pending
    "admin.verification_pending": {
        "theme": "amber",
        "icon": "&#128737;",
        "title": "Verification Pending",
        "highlight": "KYC Review!",
        "subtitle": "An enterprise organization has submitted documents requesting verification:",
        "cta_text": "Review Verification Dossier",
        "secondary_cta_text": "All Companies",
    },
    # 25. Super Admin: Verification Decision Audit
    "admin.verification_decision": {
        "theme": "slate",
        "icon": "&#128221;",
        "title": "Verification Decision",
        "highlight": "Audit Copy!",
        "subtitle": "An audited corporate verification decision was recorded by an administrator:",
        "cta_text": "View Audit Trail",
        "secondary_cta_text": "Compliance Console",
    },
    # 26. Super Admin: New User Registration
    "admin.new_user": {
        "theme": "blue",
        "icon": "&#128100;",
        "title": "New User",
        "highlight": "Registered!",
        "subtitle": "A new individual user account has registered or joined a workspace:",
        "cta_text": "View Users in Admin Panel",
        "secondary_cta_text": "User Management",
    },
    # 27. Super Admin: Subscription Change
    "admin.subscription_change": {
        "theme": "emerald",
        "icon": "&#128176;",
        "title": "Subscription Event",
        "highlight": "Revenue!",
        "subtitle": "A corporate subscription purchase, upgrade, or cancellation was processed:",
        "cta_text": "View Billing Analytics",
        "secondary_cta_text": "Invoice Ledger",
    },
    # 28. Super Admin: Payment Issue
    "admin.payment_issue": {
        "theme": "rose",
        "icon": "&#9888;",
        "title": "Billing Issue",
        "highlight": "Critical!",
        "subtitle": "A customer payment failure or auto-renewal billing issue requires intervention:",
        "cta_text": "Inspect Payment in Admin Panel",
        "secondary_cta_text": "Billing Desk",
    },
    # 29. Super Admin: Email Delivery Failure
    "admin.email_delivery_failure": {
        "theme": "rose",
        "icon": "&#9888;",
        "title": "Delivery Failure",
        "highlight": "SMTP Alert!",
        "subtitle": "A transactional email delivery exhausted all retry attempts and failed permanently:",
        "cta_text": "Inspect Failed Log",
        "secondary_cta_text": "SMTP Provider Settings",
    },
    "admin.delivery_retry_exhausted": {
        "theme": "rose",
        "icon": "&#9888;",
        "title": "Delivery Failure",
        "highlight": "SMTP Alert!",
        "subtitle": "A transactional email delivery exhausted all retry attempts and failed permanently:",
        "cta_text": "Inspect Failed Log",
        "secondary_cta_text": "SMTP Provider Settings",
    },
    # 30. Super Admin: Security Alert
    "admin.security_alert": {
        "theme": "rose",
        "icon": "&#128680;",
        "title": "Security Incident",
        "highlight": "Threat Alert!",
        "subtitle": "Repeated authentication failures or suspicious platform telemetry detected:",
        "cta_text": "View Security Audit Logs",
        "secondary_cta_text": "Access Control Desk",
    },
}

# Aliases for sales.* and vendor.* event keys
EVENT_BANNER_CONFIGS["vendor.inquiry_received"] = EVENT_BANNER_CONFIGS.get("seller.inquiry_received", {})
EVENT_BANNER_CONFIGS["sales.matching_suppliers_found"] = EVENT_BANNER_CONFIGS.get("buyer.matching_suppliers_found", {})
EVENT_BANNER_CONFIGS["sales.search_completed"] = EVENT_BANNER_CONFIGS.get("buyer.search_completed", {})
EVENT_BANNER_CONFIGS["vendor.new_buyer_lead"] = EVENT_BANNER_CONFIGS.get("seller.new_buyer_lead", {})
EVENT_BANNER_CONFIGS["vendor.new_sales_lead"] = EVENT_BANNER_CONFIGS.get("seller.new_buyer_lead", {})
EVENT_BANNER_CONFIGS["vendor.search_completed"] = EVENT_BANNER_CONFIGS.get("seller.search_completed", {})


def ensure_absolute_url(url):
    """
    Guarantees that a URL is fully qualified with the platform domain.
    Converts relative paths like '/company/billing/' to 'https://tradeconnect.com/company/billing/'.
    """
    if not url:
        return ""
    url_str = str(url).strip()
    if url_str.startswith("http://") or url_str.startswith("https://") or url_str.startswith("mailto:"):
        return url_str
    base_url = getattr(settings, "PLATFORM_BASE_URL", "").rstrip("/")
    if not base_url:
        base_url = getattr(settings, "BACKEND_BASE_URL", "").rstrip("/")
    if not base_url:
        base_url = "https://tradeconnect.com"
    return f"{base_url}/{url_str.lstrip('/')}"


def build_branded_html(
    title,
    summary="",
    details=None,
    cta_url=None,
    cta_text="View in Platform",
    severity="INFO",
    custom_content=None,
    footer_note=None,
    banner_theme=None,
    banner_icon=None,
    banner_highlight=None,
    secondary_cta_url=None,
    secondary_cta_text=None,
    preferences_url=None,
    unsubscribe_url=None,
    event_key=None,
    template_obj=None,
    layout_setting=None,
    custom_header_html=None,
    custom_footer_html=None,
):
    """
    Renders a responsive, modern HTML email matching the TradeConnect reference layout.
    Features:
    1. Standard Header: TradeConnect Logo + Tagline + Platform Slogan (FIXED ACROSS ALL TEMPLATES)
    2. Event-specific Hero Banner: Circle icon badge + Two-tone Bold Headline + Subtitle
    3. Dynamic Body Section: Greeting + Detail Cards + Next Step Action Card with Primary/Secondary CTAs
    4. Trust Badges Row: Secure & Verified, Trusted Platform, 24/7 Support (FIXED ACROSS ALL TEMPLATES)
    5. Dark Brand Footer: Contact channels, social links, preferences & unsubscribe (FIXED ACROSS ALL TEMPLATES)
    """
    themes = {
        "blue": {
            "gradient": "linear-gradient(135deg, #094a9a 0%, #0d5cb6 50%, #1e40af 100%)",
            "highlight": "#38bdf8",
            "icon_color": "#0284c7",
            "btn_bg": "#0284c7",
            "icon_char": "&#9993;",  # Envelope
        },
        "emerald": {
            "gradient": "linear-gradient(135deg, #065f46 0%, #059669 50%, #10b981 100%)",
            "highlight": "#6ee7b7",
            "icon_color": "#059669",
            "btn_bg": "#059669",
            "icon_char": "&#10004;",  # Checkmark
        },
        "amber": {
            "gradient": "linear-gradient(135deg, #92400e 0%, #b45309 50%, #d97706 100%)",
            "highlight": "#fde047",
            "icon_color": "#d97706",
            "btn_bg": "#d97706",
            "icon_char": "&#9888;",  # Warning
        },
        "rose": {
            "gradient": "linear-gradient(135deg, #991b1b 0%, #b91c1c 50%, #dc2626 100%)",
            "highlight": "#fca5a5",
            "icon_color": "#dc2626",
            "btn_bg": "#dc2626",
            "icon_char": "&#9888;",  # Alert
        },
        "purple": {
            "gradient": "linear-gradient(135deg, #3730a3 0%, #4f46e5 50%, #6366f1 100%)",
            "highlight": "#a5b4fc",
            "icon_color": "#6366f1",
            "btn_bg": "#4f46e5",
            "icon_char": "&#10024;",  # Sparkles / AI
        },
        "teal": {
            "gradient": "linear-gradient(135deg, #115e59 0%, #0d9488 50%, #14b8a6 100%)",
            "highlight": "#5eead4",
            "icon_color": "#0d9488",
            "btn_bg": "#0d9488",
            "icon_char": "&#128196;",  # Document
        },
    }

    # Pull preset banner configuration if available
    banner_cfg = EVENT_BANNER_CONFIGS.get(event_key or "", {})

    theme_key = banner_theme or banner_cfg.get("theme") or ("rose" if severity == "CRITICAL" else ("amber" if severity == "WARNING" else "blue"))
    theme = themes.get(theme_key, themes["blue"])

    # Determine Title & Highlight word
    clean_title = str(title or banner_cfg.get("title") or "Notification Alert").strip()
    highlight_word = banner_highlight or banner_cfg.get("highlight")
    main_title_text = banner_cfg.get("title") or clean_title

    if not highlight_word:
        words = clean_title.split()
        if len(words) > 1 and any(w.lower() in ("received!", "found!", "completed!", "ready!", "alert!", "successful!", "warning!", "soon!", "registered!") for w in words):
            highlight_word = words[-1]
            main_title_text = " ".join(words[:-1])
        elif len(words) > 1:
            highlight_word = words[-1]
            main_title_text = " ".join(words[:-1])
        else:
            highlight_word = "Notice"

    icon_display = banner_icon or banner_cfg.get("icon") or theme["icon_char"]
    subtitle_display = summary or banner_cfg.get("subtitle") or "Here are the operational details regarding your account."
    cta_text = cta_text or banner_cfg.get("cta_text") or "View in Platform"
    secondary_cta_text = secondary_cta_text or banner_cfg.get("secondary_cta_text")

    # Build Details Card (2-column key-value grid)
    details_html = ""
    if details and isinstance(details, dict):
        rows = ""
        items = list(details.items())
        # Render 2-column key-values table
        for i in range(0, len(items), 2):
            k1, v1 = items[i]
            col1 = f"""
            <td width="50%" style="vertical-align: top; padding: 6px 12px 6px 0;">
                <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: capitalize;">{escape(str(k1))}</span>
                <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 2px;">{escape(str(v1))}</span>
            </td>
            """
            if i + 1 < len(items):
                k2, v2 = items[i + 1]
                col2 = f"""
                <td width="50%" style="vertical-align: top; padding: 6px 0 6px 12px;">
                    <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: capitalize;">{escape(str(k2))}</span>
                    <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 2px;">{escape(str(v2))}</span>
                </td>
                """
            else:
                col2 = '<td width="50%"></td>'
            rows += f"<tr>{col1}{col2}</tr>"

        details_html = f"""
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin: 18px 0 16px;">
            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                    <td style="padding-bottom: 12px; border-bottom: 1px solid #e2e8f0;">
                        <span style="display: inline-block; width: 22px; height: 22px; border-radius: 50%; background: #e0f2fe; color: #0284c7; text-align: center; line-height: 22px; font-size: 12px; vertical-align: middle; margin-right: 6px;">&#9733;</span>
                        <span style="font-size: 13px; font-weight: 800; color: #0f172a; vertical-align: middle;">Operational Details</span>
                    </td>
                </tr>
                <tr>
                    <td style="padding-top: 10px;">
                        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                            {rows}
                        </table>
                    </td>
                </tr>
            </table>
        </div>
        """

    # Next Step & CTA Box
    cta_box_html = ""
    if cta_url:
        secondary_btn_html = ""
        if secondary_cta_url and secondary_cta_text:
            secondary_btn_html = f"""
            <div style="margin-top: 10px;">
                <a href="{escape(secondary_cta_url)}" style="background-color: #ffffff; color: {theme['btn_bg']}; border: 1px solid {theme['btn_bg']}; padding: 10px 24px; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 13px; display: inline-block;">
                    {escape(secondary_cta_text)}
                </a>
            </div>
            """
        cta_box_html = f"""
        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 20px; margin: 20px 0 8px; text-align: center;">
            <div style="margin-bottom: 6px;">
                <span style="font-size: 16px; margin-right: 4px; vertical-align: middle;">&#9889;</span>
                <span style="font-size: 13px; font-weight: 800; color: #166534; vertical-align: middle;">Recommended Next Step</span>
            </div>
            <p style="font-size: 12px; color: #374151; line-height: 1.5; margin: 0 0 16px 0;">
                Click the direct button below to access this record in your authenticated enterprise workspace.
            </p>
            <div>
                <a href="{escape(cta_url)}" style="background-color: {theme['btn_bg']}; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 800; font-size: 14px; display: inline-block; box-shadow: 0 2px 6px rgba(0,0,0,0.12);">
                    {escape(cta_text)} &rarr;
                </a>
            </div>
            {secondary_btn_html}
        </div>
        """

    body_slot = custom_content if custom_content else f"""
    <p style="font-size: 13px; color: #334155; line-height: 1.6; margin-top: 0;">
        {escape(summary)}
    </p>
    {details_html}
    {cta_box_html}
    """

    prefs_link = escape(ensure_absolute_url(preferences_url or "/company/notification-preferences/"))
    unsub_link = escape(ensure_absolute_url(unsubscribe_url or "/notifications/unsubscribe/"))

    html = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
  <style>
    body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    body {{ margin: 0; padding: 0; width: 100% !important; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; }}
    @media only screen and (max-width: 640px) {{
      .email-container {{ width: 100% !important; max-width: 100% !important; border-radius: 0 !important; }}
      .mobile-padding {{ padding-left: 18px !important; padding-right: 18px !important; }}
      .hide-mobile {{ display: none !important; }}
    }}
  </style>
</head>
<body style="background-color: #f1f5f9; margin: 0; padding: 24px 0;">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
    <tr>
      <td align="center" style="padding: 12px;">
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" class="email-container" style="max-width: 640px; background-color: #ffffff; border-radius: 16px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 15px rgba(0,0,0,0.04);">
          <!-- 1. STANDARD HEADER -->
          <tr>
            <td style="background-color: #ffffff; padding: 24px 32px 20px; border-bottom: 1px solid #f1f5f9;" class="mobile-padding">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td align="left" style="vertical-align: middle;">
                    <table role="presentation" border="0" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="vertical-align: middle; padding-right: 12px;">
                          <div style="width: 38px; height: 38px; border-radius: 10px; background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%); text-align: center; line-height: 38px; box-shadow: 0 2px 8px rgba(37,99,235,0.25);">
                            <span style="color: #ffffff; font-size: 20px; font-weight: 900; line-height: 38px; display: inline-block;">&#9650;</span>
                          </div>
                        </td>
                        <td style="vertical-align: middle;">
                          <span style="font-size: 21px; font-weight: 900; color: #0f172a; letter-spacing: -0.5px; display: block; line-height: 1.1;">TradeConnect</span>
                          <span style="font-size: 10px; font-weight: 700; color: #0284c7; letter-spacing: 1px; text-transform: uppercase; display: block; margin-top: 2px;">Buy &bull; Sell &bull; Grow</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td align="right" style="vertical-align: middle;" class="hide-mobile">
                    <span style="font-size: 12px; font-weight: 700; color: #334155; display: block; line-height: 1.2;">B2B Procurement &amp; Sales Platform</span>
                    <span style="font-size: 10px; color: #64748b; display: block; margin-top: 2px;">Smarter Sourcing. Stronger Partnerships.</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- 2. EVENT-SPECIFIC HERO BANNER -->
          <tr>
            <td style="background: {theme['gradient']}; padding: 32px 32px 28px; color: #ffffff;" class="mobile-padding">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td width="72" style="vertical-align: middle; padding-right: 18px;">
                    <div style="width: 64px; height: 64px; border-radius: 50%; background: #ffffff; text-align: center; line-height: 64px; box-shadow: 0 4px 14px rgba(0,0,0,0.18);">
                      <span style="font-size: 28px; color: {theme['icon_color']}; line-height: 64px;">{icon_display}</span>
                    </div>
                  </td>
                  <td style="vertical-align: middle;">
                    <h1 style="margin: 0 0 6px 0; font-size: 23px; font-weight: 900; color: #ffffff; line-height: 1.2; letter-spacing: -0.5px;">
                      {escape(main_title_text)} <span style="color: {theme['highlight']};">{escape(highlight_word)}</span>
                    </h1>
                    <p style="margin: 0; font-size: 13px; color: #e0f2fe; line-height: 1.45; font-weight: 500;">
                      {escape(summary)}
                    </p>
                  </td>
                  <td width="60" align="right" style="vertical-align: middle; opacity: 0.18;" class="hide-mobile">
                    <span style="font-size: 46px; line-height: 1;">&#129309;</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- 3. DYNAMIC BODY CONTENT -->
          <tr>
            <td style="padding: 32px 32px 24px; background-color: #ffffff;" class="mobile-padding">
              {body_slot}
            </td>
          </tr>

          <!-- 4. TRUST BADGES ROW -->
          <tr>
            <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; padding: 18px 24px;" class="mobile-padding">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td width="33%" align="center" style="vertical-align: middle; border-right: 1px solid #e2e8f0; padding: 4px 8px;">
                    <span style="font-size: 15px; margin-right: 4px; vertical-align: middle;">&#128737;</span>
                    <span style="font-size: 11px; font-weight: 700; color: #475569; vertical-align: middle;">Secure &amp; Verified</span>
                  </td>
                  <td width="34%" align="center" style="vertical-align: middle; border-right: 1px solid #e2e8f0; padding: 4px 8px;">
                    <span style="font-size: 15px; margin-right: 4px; vertical-align: middle;">&#9745;</span>
                    <span style="font-size: 11px; font-weight: 700; color: #475569; vertical-align: middle;">Trusted B2B Platform</span>
                  </td>
                  <td width="33%" align="center" style="vertical-align: middle; padding: 4px 8px;">
                    <span style="font-size: 15px; margin-right: 4px; vertical-align: middle;">&#127911;</span>
                    <span style="font-size: 11px; font-weight: 700; color: #475569; vertical-align: middle;">24/7 Support</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- 5. DARK BRAND FOOTER -->
          <tr>
            <td style="background-color: #0f172a; padding: 24px 32px; color: #94a3b8; font-size: 11px;" class="mobile-padding">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td align="left" style="vertical-align: middle;">
                    <span style="color: #ffffff; font-weight: 800; font-size: 13px;">TradeConnect</span>
                    <span style="color: #475569; margin: 0 8px;">|</span>
                    <a href="https://tradeconnect.com" style="color: #94a3b8; text-decoration: none;">www.tradeconnect.com</a>
                    <span style="color: #475569; margin: 0 8px;">|</span>
                    <a href="mailto:support@tradeconnect.com" style="color: #94a3b8; text-decoration: none;">support@tradeconnect.com</a>
                  </td>
                  <td align="right" style="vertical-align: middle;" class="hide-mobile">
                    <span style="color: #cbd5e1; font-weight: 700; font-size: 11px; margin-left: 10px;">in</span>
                    <span style="color: #cbd5e1; font-weight: 700; font-size: 11px; margin-left: 10px;">fb</span>
                    <span style="color: #cbd5e1; font-weight: 700; font-size: 11px; margin-left: 10px;">yt</span>
                    <span style="color: #cbd5e1; font-weight: 700; font-size: 11px; margin-left: 10px;">&#120143;</span>
                  </td>
                </tr>
                <tr>
                  <td colspan="2" style="padding-top: 14px; border-top: 1px solid #1e293b; margin-top: 12px; color: #64748b; font-size: 10px; line-height: 1.5;">
                    You received this operational email because your company account is registered on the <strong>TradeConnect B2B AI Procurement &amp; Sales Platform</strong>.<br>
                    Manage your <a href="{prefs_link}" style="color: #38bdf8; text-decoration: underline;">notification preferences</a> or <a href="{unsub_link}" style="color: #38bdf8; text-decoration: underline;">unsubscribe</a>.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    try:
        from django.template.loader import render_to_string

        # Resolve layout settings and template overrides
        if not layout_setting:
            try:
                layout_setting = EmailLayoutSetting.get_active_layout()
            except Exception:
                layout_setting = None

        header_html_to_use = custom_header_html
        footer_html_to_use = custom_footer_html

        if template_obj and getattr(template_obj, "use_custom_layout", False):
            if template_obj.header_html:
                header_html_to_use = template_obj.header_html
            if template_obj.footer_html:
                footer_html_to_use = template_obj.footer_html

        if not header_html_to_use and layout_setting:
            if layout_setting.use_custom_header_html and layout_setting.custom_header_html:
                header_html_to_use = layout_setting.custom_header_html

        if not footer_html_to_use and layout_setting:
            if layout_setting.use_custom_footer_html and layout_setting.custom_footer_html:
                footer_html_to_use = layout_setting.custom_footer_html

        email_context = {
            "email_subject": clean_title,
            "banner_gradient": theme["gradient"],
            "banner_icon_color": theme["icon_color"],
            "banner_icon_html": f'<span style="font-size: 28px; color: {theme["icon_color"]}; line-height: 64px;">{icon_display}</span>',
            "banner_title": main_title_text,
            "banner_highlight": highlight_word,
            "banner_highlight_color": theme["highlight"],
            "banner_subtitle": subtitle_display,
            "email_body": body_slot,
            "preferences_url": prefs_link,
            "unsubscribe_url": unsub_link,
            # Dynamic Header fields
            "custom_header_html": header_html_to_use,
            "brand_name": getattr(layout_setting, "brand_name", "TradeConnect") if layout_setting else "TradeConnect",
            "brand_tagline": str(getattr(layout_setting, "brand_tagline", "Buy &bull; Sell &bull; Grow") if layout_setting else "Buy &bull; Sell &bull; Grow").replace("•", "&bull;"),
            "header_title": getattr(layout_setting, "header_title", "B2B Procurement & Sales Platform") if layout_setting else "B2B Procurement & Sales Platform",
            "header_subtitle": getattr(layout_setting, "header_subtitle", "Smarter Sourcing. Stronger Partnerships.") if layout_setting else "Smarter Sourcing. Stronger Partnerships.",
            "logo_url": getattr(layout_setting, "logo_url", "") if layout_setting else "",
            "header_bg_color": getattr(layout_setting, "header_bg_color", "#ffffff") if layout_setting else "#ffffff",
            "header_text_color": getattr(layout_setting, "header_text_color", "#0f172a") if layout_setting else "#0f172a",
            # Trust Badges
            "show_trust_badges": getattr(layout_setting, "show_trust_badges", True) if layout_setting else True,
            "badge_1_icon": getattr(layout_setting, "badge_1_icon", "🛡️") if layout_setting else "🛡️",
            "badge_1_text": getattr(layout_setting, "badge_1_text", "Secure &amp; Verified") if layout_setting else "Secure &amp; Verified",
            "badge_2_icon": getattr(layout_setting, "badge_2_icon", "☑️") if layout_setting else "☑️",
            "badge_2_text": getattr(layout_setting, "badge_2_text", "Trusted B2B Platform") if layout_setting else "Trusted B2B Platform",
            "badge_3_icon": getattr(layout_setting, "badge_3_icon", "🎧") if layout_setting else "🎧",
            "badge_3_text": getattr(layout_setting, "badge_3_text", "24/7 Support") if layout_setting else "24/7 Support",
            # Dynamic Footer fields
            "custom_footer_html": footer_html_to_use,
            "footer_brand_name": getattr(layout_setting, "footer_brand_name", "TradeConnect") if layout_setting else "TradeConnect",
            "website_url": getattr(layout_setting, "website_url", "https://tradeconnect.com") if layout_setting else "https://tradeconnect.com",
            "website_display": "www.tradeconnect.com" if "tradeconnect" in str(getattr(layout_setting, "website_url", "https://tradeconnect.com")).lower() else str(getattr(layout_setting, "website_url", "")).replace("https://", "").replace("http://", "").strip("/"),
            "support_email": getattr(layout_setting, "support_email", "support@tradeconnect.com") if layout_setting else "support@tradeconnect.com",
            "compliance_text": getattr(layout_setting, "compliance_text", "") if layout_setting else "You received this operational email because your company account is registered on the TradeConnect B2B AI Procurement & Sales Platform.",
            "copyright_text": getattr(layout_setting, "copyright_text", "© 2026 TradeConnect Inc. All rights reserved.") if layout_setting else "© 2026 TradeConnect Inc. All rights reserved.",
            "social_linkedin": getattr(layout_setting, "social_linkedin", "") if layout_setting else "https://linkedin.com",
            "social_facebook": getattr(layout_setting, "social_facebook", "") if layout_setting else "https://facebook.com",
            "social_youtube": getattr(layout_setting, "social_youtube", "") if layout_setting else "https://youtube.com",
            "social_twitter": getattr(layout_setting, "social_twitter", "") if layout_setting else "https://twitter.com",
        }
        return render_to_string("notifications/emails/base_email.html", email_context)
    except Exception as exc:
        logger.debug(f"render_to_string base_email fallback: {exc}")
        return html


def render_string_template(template_str, context_dict):
    """Safely renders a Django string template with a dict context."""
    try:
        t = Template(template_str)
        c = Context(context_dict or {})
        return t.render(c)
    except Exception as exc:
        logger.warning(f"Template rendering fallback triggered: {exc}")
        # Safe fallback: standard string replace
        res = template_str
        for k, v in (context_dict or {}).items():
            res = res.replace(f"{{{{ {k} }}}}", str(v)).replace(f"{{{{{k}}}}}", str(v))
        return res


def trigger_email_notification(
    event_key,
    context=None,
    company=None,
    user=None,
    idempotency_key=None,
    severity=None,
    priority=None,
    async_dispatch=True,
    attachments=None,
    is_manual=False,
    sent_by=None,
    custom_recipient_emails=None,
    override_subject=None,
):
    """
    Central entry point for all platform modules to trigger automated or manual email notifications.
    Matches active rules, resolves recipients, enforces delivery modes (AUTOMATIC, MANUAL, BOTH),
    checks cooldowns/thresholds, renders templates, writes to outbox (NotificationLog),
    and initiates delivery without blocking the calling request. Supports binary/file attachments.
    """
    context = context or {}
    created_logs = []

    # 1. Look up rules matching this event key (support both sales.* and buyer.*, vendor.* and seller.*)
    lookup_keys = [event_key]
    if event_key.startswith("sales."):
        lookup_keys.append("buyer." + event_key[6:])
    elif event_key.startswith("buyer."):
        lookup_keys.append("sales." + event_key[6:])
    elif event_key.startswith("vendor."):
        lookup_keys.append("seller." + event_key[7:])
    elif event_key.startswith("seller."):
        lookup_keys.append("vendor." + event_key[7:])

    rules = list(NotificationRule.objects.filter(event_key__in=lookup_keys).select_related("template"))

    # If company is provided, filter rules matching company_type scope
    if company:
        c_type = getattr(company, "company_type", "")
        is_sales = getattr(company, "is_sales_type", c_type in ("BUYER", "SALES"))
        is_vendor = getattr(company, "is_vendor_type", c_type in ("SELLER", "VENDOR"))
        allowed_scopes = {NotificationRule.CompanyTypeScope.ALL, NotificationRule.CompanyTypeScope.BOTH}
        if is_sales:
            allowed_scopes.update({NotificationRule.CompanyTypeScope.BUYER, "SALES"})
        if is_vendor:
            allowed_scopes.update({NotificationRule.CompanyTypeScope.SELLER, "VENDOR"})
        rules = [r for r in rules if r.company_type in allowed_scopes]

    if not rules:
        # Check if a standalone active NotificationTemplate exists for this event_key
        standalone_tpl = NotificationTemplate.objects.filter(event_key__in=lookup_keys, is_active=True).first()
        if not standalone_tpl:
            logger.info(f"No active notification rule or template found for event '{event_key}'. Skipping dispatch.")
            return []
        # Synthesize a default rule
        synthetic_rule = NotificationRule(
            name=standalone_tpl.name,
            event_key=event_key,
            template=standalone_tpl,
            recipient_type=NotificationRule.RecipientType.ACTING_USER if user else NotificationRule.RecipientType.COMPANY_ADMINS,
            severity=severity or NotificationRule.Severity.INFO,
            priority=priority or NotificationRule.Priority.NORMAL,
            delivery_mode=NotificationRule.DeliveryMode.AUTOMATIC,
            is_enabled=True,
        )
        rules = [synthetic_rule]

    for rule in rules:
        tpl = rule.template
        if not tpl or not tpl.is_active:
            continue

        # Check enable switch: Mandatory auth emails (like password reset, email verification) cannot be disabled
        if not rule.is_enabled and not rule.is_mandatory_auth:
            logger.info(f"Skipping dispatch: rule '{rule.name}' ({event_key}) is disabled.")
            continue

        # Delivery Mode contract:
        # AUTOMATIC: triggered only on business events; manual send blocked.
        # MANUAL: triggered only on explicit admin UI actions; automatic trigger skipped.
        # BOTH: supports both automatic business trigger and manual admin resend.
        if not is_manual:
            if rule.delivery_mode == NotificationRule.DeliveryMode.MANUAL:
                logger.info(f"Skipping automatic dispatch for '{rule.name}' because delivery_mode is MANUAL only.")
                continue
        else:
            is_staff_test = bool(custom_recipient_emails and sent_by and (getattr(sent_by, "is_staff", False) or getattr(sent_by, "is_superuser", False)))
            if not is_staff_test and (not rule.allow_manual_send or rule.delivery_mode == NotificationRule.DeliveryMode.AUTOMATIC):
                logger.info(f"Skipping manual dispatch for '{rule.name}' because delivery_mode is AUTOMATIC only.")
                continue

        # Business Rule: Low Credits Threshold & Cooldown Check
        if event_key == "billing.credits_low" and company:
            from decimal import Decimal
            threshold = Decimal(str(rule.threshold_value)) if rule.threshold_value is not None else Decimal("50.00")
            from apps.billing.models import CompanyCreditWallet
            wallet = CompanyCreditWallet.objects.filter(company=company).first()
            current_bal = None
            if "current_balance" in context:
                try:
                    current_bal = Decimal(str(context["current_balance"]))
                except Exception:
                    pass
            if current_bal is None and wallet:
                current_bal = Decimal(str(wallet.total_available))

            if current_bal is not None and current_bal > threshold:
                logger.info(f"Skipping low credits alert: company {company.name} available credits {current_bal} > threshold {threshold}")
                continue

            cooldown_hrs = rule.cooldown_hours or 24
            recent_alert = NotificationLog.objects.filter(
                company=company,
                event_key=event_key,
                created_at__gte=timezone.now() - timezone.timedelta(hours=cooldown_hrs)
            ).exists()
            if recent_alert:
                logger.info(f"Skipping low credits notification for {company.name} due to active {cooldown_hrs}h cooldown.")
                continue

        # 2. Resolve recipients
        if custom_recipient_emails:
            recipients = [
                {"email": em.strip(), "name": em.strip().split("@")[0].title(), "user": None}
                for em in custom_recipient_emails if em and em.strip()
            ]
        else:
            recipients = resolve_rule_recipients(rule, company=company, user=user, context=context)

        # Parse CC / BCC
        cc_list = [c.strip() for c in (rule.cc_recipients or "").split(",") if c.strip()]
        bcc_list = [b.strip() for b in (rule.bcc_recipients or "").split(",") if b.strip()]

        # Determine channel configuration (default: BOTH)
        rule_channel = getattr(rule, "channel", NotificationRule.Channel.BOTH)

        for rec in recipients:
            email = rec["email"]
            name = rec["name"]
            user_obj = rec.get("user")

            # 3. Deterministic Idempotency Key
            per_rec_idempotency = f"{idempotency_key}_{email}" if idempotency_key else None
            if per_rec_idempotency:
                existing = NotificationLog.objects.filter(idempotency_key=per_rec_idempotency).first()
                if existing:
                    logger.info(f"Idempotent notification skipped (already logged): {per_rec_idempotency}")
                    continue

            # 4. Context Preparation
            merged_context = {
                "recipient_name": name,
                "recipient_email": email,
                "company_name": company.name if company else (user_obj.company.name if user_obj and getattr(user_obj, "company", None) else "Your Company"),
                "platform_name": "AI Procurement & Sales Platform",
                "current_year": timezone.now().year,
                **context,
            }

            # 5. Render Subject and Content
            subj_raw = override_subject or rule.subject_override or tpl.subject
            subject_rendered = render_string_template(subj_raw, merged_context).strip()
            body_rendered = render_string_template(tpl.html_content, merged_context)
            text_rendered = render_string_template(tpl.text_content, merged_context) if tpl.text_content else ""

            # --- IN-APP SYSTEM NOTIFICATION CHANNEL ---
            in_app_channels = (
                NotificationRule.Channel.IN_APP,
                NotificationRule.Channel.BOTH,
                NotificationRule.Channel.IN_APP_PUSH,
                NotificationRule.Channel.ALL,
            )
            if rule_channel in in_app_channels and user_obj:
                try:
                    from apps.dashboard.models import Notification
                    from apps.notifications.models import NotificationPreference
                    if NotificationPreference.is_in_app_permitted(user_obj, tpl.category if tpl else "SYSTEM"):
                        # Map priority
                        if rule.priority == NotificationRule.Priority.URGENT or rule.severity == NotificationRule.Severity.CRITICAL:
                            notif_priority = Notification.Priority.CRITICAL
                        elif rule.priority == NotificationRule.Priority.HIGH or rule.severity == NotificationRule.Severity.WARNING:
                            notif_priority = Notification.Priority.WARNING
                        elif any(w in event_key.lower() for w in ["success", "won", "approved", "recharge"]):
                            notif_priority = Notification.Priority.SUCCESS
                        else:
                            notif_priority = Notification.Priority.INFO

                        banner_cfg = EVENT_BANNER_CONFIGS.get(event_key, {})
                        theme = banner_cfg.get("theme", "blue")
                        theme_color_map = {
                            "blue": "blue",
                            "emerald": "emerald",
                            "amber": "amber",
                            "rose": "rose",
                            "purple": "purple",
                            "teal": "emerald",
                        }
                        theme_icon_map = {
                            "blue": "fa-bell",
                            "emerald": "fa-circle-check",
                            "amber": "fa-triangle-exclamation",
                            "rose": "fa-circle-exclamation",
                            "purple": "fa-wand-magic-sparkles",
                            "teal": "fa-file-invoice",
                        }
                        event_icon_map = {
                            "buyer.rfq_dispatched": "fa-paper-plane",
                            "sales.rfq_dispatched": "fa-paper-plane",
                            "seller.inquiry_reply": "fa-reply",
                            "vendor.inquiry_reply": "fa-reply",
                            "buyer.quote_recorded": "fa-file-invoice-dollar",
                            "sales.quote_recorded": "fa-file-invoice-dollar",
                            "billing.wallet_recharge_success": "fa-wallet",
                            "billing.wallet_recharged": "fa-wallet",
                            "billing.invoice_generated": "fa-file-invoice",
                            "billing.credits_low": "fa-battery-quarter",
                            "billing.low_wallet_balance": "fa-battery-quarter",
                            "billing.low_balance": "fa-battery-quarter",
                            "billing.subscription_expiring": "fa-calendar-clock",
                            "billing.payment_failed": "fa-credit-card",
                            "search.ai_completed": "fa-brain",
                            "sales.matching_suppliers_found": "fa-handshake",
                            "buyer.matching_suppliers_found": "fa-handshake",
                            "vendor.new_buyer_lead": "fa-bullhorn",
                            "seller.new_buyer_lead": "fa-bullhorn",
                            "company.user_added": "fa-user-plus",
                            "company.verification_decision": "fa-building-circle-check",
                            "auth.password_reset": "fa-key",
                            "auth.password_changed": "fa-shield-halved",
                            "auth.welcome": "fa-door-open",
                            "auth.email_verification": "fa-envelope-circle-check",
                            "admin.critical_system_alert": "fa-triangle-exclamation",
                            "admin.security_alert": "fa-shield-virus",
                        }
                        in_app_color = theme_color_map.get(theme, "blue")
                        in_app_icon = event_icon_map.get(event_key) or theme_icon_map.get(theme, "fa-bell")

                        summary_msg = str(context.get("summary") or subject_rendered)
                        cta_nav_link = str(context.get("cta_url") or "")

                        # Enhanced duplicate prevention (ensure same item/action is not duplicated within 15 min window)
                        dup_filter = Q(
                            user=user_obj,
                            title=subject_rendered[:250],
                            created_at__gte=timezone.now() - timezone.timedelta(minutes=15),
                        )
                        if event_key:
                            dup_filter &= (Q(event_key=event_key) | Q(event_key=""))
                        if cta_nav_link:
                            dup_filter &= Q(link=cta_nav_link[:500])

                        already_notified = Notification.objects.filter(dup_filter).exists()

                        if not already_notified:
                            Notification.objects.create(
                                user=user_obj,
                                company=company or getattr(user_obj, "company", None),
                                notification_type=Notification.NotificationType.SYSTEM,
                                priority=notif_priority,
                                event_key=event_key,
                                title=subject_rendered[:250],
                                message=summary_msg[:1000],
                                link=cta_nav_link[:500],
                                icon=in_app_icon,
                                color=in_app_color,
                            )
                except Exception as notif_err:
                    logger.warning(f"Error creating in-app notification: {notif_err}")

            # --- FIREBASE CLOUD MESSAGING (WEB PUSH) CHANNEL ---
            push_channels = (
                NotificationRule.Channel.PUSH,
                NotificationRule.Channel.IN_APP_PUSH,
                NotificationRule.Channel.ALL,
            )
            if rule_channel in push_channels and user_obj:
                try:
                    from apps.notifications.services.fcm_service import send_user_push
                    cat_key = tpl.category if tpl else "SYSTEM"
                    summary_msg = str(context.get("summary") or subject_rendered)
                    cta_nav_link = str(context.get("cta_url") or "")
                    send_user_push(
                        user_or_users=user_obj,
                        title=subject_rendered[:250],
                        body=summary_msg[:500],
                        link=cta_nav_link,
                        category=cat_key,
                    )
                except Exception as push_err:
                    logger.warning(f"Error dispatching FCM Web Push: {push_err}")

            # If rule channel does not include email, skip email outbox log & dispatch!
            non_email_channels = (
                NotificationRule.Channel.IN_APP,
                NotificationRule.Channel.PUSH,
                NotificationRule.Channel.IN_APP_PUSH,
            )
            if rule_channel in non_email_channels:
                logger.info(f"Notification rule '{rule.name}' delivery is {rule_channel}. Email dispatch skipped.")
                continue

            # Strict User Notification Preference Check for Email Channel
            cat_key = tpl.category if tpl else "SYSTEM"
            if user_obj:
                from apps.notifications.models import NotificationPreference
                if not NotificationPreference.is_email_permitted(user_obj, cat_key):
                    logger.info(f"NotificationLog skipped for {email} due to user preference ({cat_key}).")
                    continue

            # Resolve 1-click tokenized unsubscribe URL for recipient
            from apps.notifications.services.unsubscribe_service import generate_unsubscribe_token
            user_for_token = user_obj or user
            u_token = generate_unsubscribe_token(user_for_token.id, cat_key) if user_for_token and getattr(user_for_token, "id", None) else ""
            token_unsub_url = ensure_absolute_url(f"/notifications/unsubscribe/?token={u_token}") if u_token else ensure_absolute_url("/company/notification-preferences/")

            # If template content does not contain HTML tags, wrap it in our responsive branded layout
            if "<html" not in body_rendered.lower():
                banner_cfg = EVENT_BANNER_CONFIGS.get(event_key, {})
                final_html = build_branded_html(
                    title=banner_cfg.get("title", subject_rendered),
                    summary=context.get("summary") or banner_cfg.get("subtitle", ""),
                    details=context.get("details"),
                    cta_url=context.get("cta_url"),
                    cta_text=context.get("cta_text", banner_cfg.get("cta_text", "View in Platform")),
                    secondary_cta_url=context.get("secondary_cta_url") or context.get("dashboard_url"),
                    secondary_cta_text=context.get("secondary_cta_text", banner_cfg.get("secondary_cta_text")),
                    severity=rule.severity,
                    custom_content=body_rendered,
                    preferences_url="/company/notification-preferences/",
                    unsubscribe_url=token_unsub_url,
                    event_key=event_key,
                    template_obj=tpl,
                )
            else:
                final_html = body_rendered

            if not text_rendered:
                text_rendered = f"{subject_rendered}\n\n{context.get('summary', '')}\n\nVisit: {context.get('cta_url', '')}"

            att_names = []
            if attachments:
                for a in attachments:
                    if isinstance(a, tuple):
                        att_names.append(str(a[0]))
                    else:
                        att_names.append(str(getattr(a, "name", "attachment")))

            # 6. Save Outbox Log
            log = NotificationLog.objects.create(
                event_key=event_key,
                template=tpl,
                company=company,
                user=user_obj or user,
                recipient_email=email,
                recipient_name=name,
                subject=subject_rendered,
                html_body=final_html,
                text_body=text_rendered,
                context_data={k: str(v) for k, v in context.items() if not any(s in k.lower() for s in ["password", "secret", "token", "key"])},
                status=NotificationLog.Status.QUEUED,
                severity=rule.severity,
                idempotency_key=per_rec_idempotency,
                has_attachments=bool(attachments),
                attachment_names=att_names,
                delivery_mode=NotificationRule.DeliveryMode.MANUAL if is_manual else NotificationRule.DeliveryMode.AUTOMATIC,
                sent_by=sent_by,
                cc_recipients=",".join(cc_list),
                bcc_recipients=",".join(bcc_list),
            )
            created_logs.append(log)

            # 7. Dispatch
            if async_dispatch and not getattr(settings, "TESTING", False):
                threading.Thread(
                    target=deliver_notification_log,
                    args=(log.id,),
                    kwargs={"attachments": attachments},
                    daemon=True,
                ).start()
            else:
                deliver_notification_log(log.id, attachments=attachments)
                try:
                    log.refresh_from_db()
                except Exception:
                    pass

    return created_logs


def deliver_notification_log(log_or_id, attachments=None):
    """
    Executes actual email transmission for a given NotificationLog record.
    Logs success or records delivery failure attempt with sanitized traceback.
    Includes RFC 8058 headers and supports binary attachments, CC, and BCC.
    """
    if isinstance(log_or_id, NotificationLog):
        log = log_or_id
    else:
        try:
            log = NotificationLog.objects.select_related("template").get(id=log_or_id)
        except (NotificationLog.DoesNotExist, ValueError, TypeError):
            return False

    if log.status == NotificationLog.Status.SENT:
        return True

    if log.attempts_count >= log.max_attempts:
        log.status = NotificationLog.Status.FAILED
        log.save(update_fields=["status"])
        return False

    attempt_number = log.attempts_count + 1

    try:
        connection, _ = get_active_email_connection()
        from_email = get_default_from_email()

        log.status = NotificationLog.Status.SENDING
        log.save(update_fields=["status"])

        cc_list = [c.strip() for c in (log.cc_recipients or "").split(",") if c.strip()]
        bcc_list = [b.strip() for b in (log.bcc_recipients or "").split(",") if b.strip()]

        msg = EmailMultiAlternatives(
            subject=log.subject,
            body=log.text_body,
            from_email=from_email,
            to=[log.recipient_email],
            cc=cc_list if cc_list else None,
            bcc=bcc_list if bcc_list else None,
            connection=connection,
        )
        msg.attach_alternative(log.html_body, "text/html")

        # Deliverability & RFC 8058 Compliance Headers
        from apps.notifications.services.unsubscribe_service import generate_unsubscribe_token
        cat_key = log.template.category if log.template else "SYSTEM"
        unsub_token = generate_unsubscribe_token(log.user_id, cat_key) if log.user_id else ""
        unsub_url = ensure_absolute_url(f"/notifications/unsubscribe/?token={unsub_token}") if unsub_token else ensure_absolute_url("/company/notification-preferences/")

        msg.extra_headers["List-Unsubscribe"] = f"<{unsub_url}>"
        msg.extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.extra_headers["X-Entity-Ref-ID"] = f"TC-NOTIF-{log.id}"

        # Attachments pipeline
        if attachments:
            for att in attachments:
                if isinstance(att, tuple):
                    if len(att) == 3:
                        msg.attach(att[0], att[1], att[2])
                    elif len(att) == 2:
                        msg.attach(att[0], att[1])
                elif hasattr(att, "read"):
                    if hasattr(att, "seek"):
                        att.seek(0)
                    fname = getattr(att, "name", "document.pdf")
                    msg.attach(fname, att.read())

        sent_count = msg.send(fail_silently=False)

        if sent_count > 0:
            log.status = NotificationLog.Status.SENT
            log.sent_at = timezone.now()
            log.attempts_count = attempt_number
            log.last_error = ""
            log.retry_after = None
            log.save(update_fields=["status", "sent_at", "attempts_count", "last_error", "retry_after"])

            EmailDeliveryAttempt.objects.create(
                log=log,
                attempt_number=attempt_number,
                status=EmailDeliveryAttempt.Status.SUCCESS,
            )
            logger.info(f"NotificationLog #{log.id} successfully dispatched to {log.recipient_email}")
            return True
        else:
            raise RuntimeError("Backend sent 0 messages.")

    except Exception as exc:
        err_msg = str(exc)
        logger.warning(f"NotificationLog #{log.id} failed on attempt #{attempt_number}: {err_msg}")

        log.attempts_count = attempt_number
        log.last_error = err_msg
        if log.attempts_count >= log.max_attempts:
            log.status = NotificationLog.Status.FAILED
            log.retry_after = None
        else:
            log.status = NotificationLog.Status.QUEUED
            # Exponential backoff: 2, 4, 8 minutes
            log.retry_after = timezone.now() + timezone.timedelta(minutes=2 ** attempt_number)
        log.save(update_fields=["status", "attempts_count", "last_error", "retry_after"])

        EmailDeliveryAttempt.objects.create(
            log=log,
            attempt_number=attempt_number,
            status=EmailDeliveryAttempt.Status.FAILED,
            error_message=err_msg,
        )

        # Trigger admin alert if max retry reached and not already an admin alert
        if log.status == NotificationLog.Status.FAILED and not log.event_key.startswith("admin."):
            try:
                trigger_email_notification(
                    event_key="admin.email_delivery_failure",
                    context={
                        "failed_event": log.event_key,
                        "recipient_email": log.recipient_email,
                        "subject": log.subject,
                        "attempts_count": str(log.max_attempts),
                        "log_id": str(log.id),
                        "error_message": err_msg[:120],
                        "cta_url": "/admin-panel/email/logs/",
                    },
                    severity=NotificationRule.Severity.CRITICAL,
                    idempotency_key=f"alert_exhausted_{log.id}",
                    async_dispatch=False,
                )
            except Exception:
                pass

        return False


def retry_failed_notification_log(log_or_id, async_dispatch=False):
    """
    Safe retry function callable from Super Admin panel.
    Accepts either a NotificationLog instance or an integer ID.
    Resets log status to QUEUED and increases max_attempts by 1.
    """
    try:
        if isinstance(log_or_id, NotificationLog):
            log = log_or_id
        else:
            log = NotificationLog.objects.get(id=log_or_id)
        log.max_attempts = max(log.max_attempts, log.attempts_count + 1)
        log.status = NotificationLog.Status.QUEUED
        log.save(update_fields=["max_attempts", "status"])

        if async_dispatch:
            thread = threading.Thread(
                target=deliver_notification_log,
                args=(log.id,),
                daemon=True,
            )
            thread.start()
            return True
        else:
            return deliver_notification_log(log.id)
    except Exception as exc:
        logger.error(f"Error retrying log #{getattr(log_or_id, 'id', log_or_id)}: {exc}")
        return False


def create_in_app_notification(
    user,
    title,
    message,
    link="",
    priority="INFO",
    event_key="",
    icon="fa-bell",
    color="blue",
    company=None,
):
    """
    Direct helper to create an in-app system notification for a specific user.
    """
    from apps.dashboard.models import Notification
    from apps.notifications.models import NotificationPreference

    target_company = company or getattr(user, "company", None)
    return Notification.objects.create(
        user=user,
        company=target_company,
        notification_type=Notification.NotificationType.SYSTEM,
        priority=priority,
        event_key=event_key,
        title=str(title)[:250],
        message=str(message)[:1000],
        link=str(link)[:500],
        icon=icon,
        color=color,
    )


# Unified alias for all platform events
trigger_system_notification = trigger_email_notification
