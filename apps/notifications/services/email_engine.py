import logging
import threading
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Template, Context
from django.utils import timezone
from django.utils.html import escape

from apps.notifications.models import (
    NotificationTemplate,
    NotificationRule,
    NotificationLog,
    EmailDeliveryAttempt,
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
}


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
        return render_to_string(
            "notifications/emails/base_email.html",
            {
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
            },
        )
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
):
    """
    Central entry point for all platform modules to trigger automated email notifications.
    Matches active rules, resolves recipients, renders templates, writes to outbox (NotificationLog),
    and initiates delivery without blocking the calling request. Supports binary/file attachments.
    """
    context = context or {}
    created_logs = []

    # 1. Look up active rules matching this event key
    rules = NotificationRule.objects.filter(event_key=event_key, is_enabled=True).select_related("template")

    # If company is provided, filter rules matching company_type scope
    if company:
        c_type = getattr(company, "company_type", "")
        if c_type:
            # Rule applies if company_type matches company's type or is ALL
            rules = [r for r in rules if r.company_type in (c_type, NotificationRule.CompanyTypeScope.ALL)]

    if not rules:
        # Check if a standalone active NotificationTemplate exists for this event_key
        standalone_tpl = NotificationTemplate.objects.filter(event_key=event_key, is_active=True).first()
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
        )
        rules = [synthetic_rule]

    for rule in rules:
        tpl = rule.template
        if not tpl or not tpl.is_active:
            continue

        # 2. Resolve recipients
        recipients = resolve_rule_recipients(rule, company=company, user=user, context=context)

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
            subject_rendered = render_string_template(tpl.subject, merged_context).strip()
            body_rendered = render_string_template(tpl.html_content, merged_context)
            text_rendered = render_string_template(tpl.text_content, merged_context) if tpl.text_content else ""

            # Resolve 1-click tokenized unsubscribe URL for recipient
            from apps.notifications.services.unsubscribe_service import generate_unsubscribe_token
            user_for_token = user_obj or user
            cat_key = tpl.category if tpl else "SYSTEM"
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

    return created_logs


def deliver_notification_log(log_id, attachments=None):
    """
    Executes actual email transmission for a given NotificationLog record.
    Logs success or records delivery failure attempt with sanitized traceback.
    Includes RFC 8058 headers and supports binary attachments.
    """
    try:
        log = NotificationLog.objects.select_related("template").get(id=log_id)
    except NotificationLog.DoesNotExist:
        return False

    if log.status == NotificationLog.Status.SENT:
        return True

    if log.attempts_count >= log.max_attempts:
        log.status = NotificationLog.Status.FAILED
        log.save(update_fields=["status"])
        return False

    attempt_number = log.attempts_count + 1
    connection, _ = get_active_email_connection()
    from_email = get_default_from_email()

    try:
        log.status = NotificationLog.Status.SENDING
        log.save(update_fields=["status"])

        msg = EmailMultiAlternatives(
            subject=log.subject,
            body=log.text_body,
            from_email=from_email,
            to=[log.recipient_email],
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
                    event_key="admin.delivery_retry_exhausted",
                    context={
                        "summary": f"Email delivery failed after {log.max_attempts} attempts for recipient: {log.recipient_email}",
                        "details": {
                            "Failed Event": log.event_key,
                            "Recipient": log.recipient_email,
                            "Subject": log.subject,
                            "Error": err_msg[:120],
                        },
                        "cta_url": "/admin-panel/email/logs/",
                    },
                    severity=NotificationRule.Severity.WARNING,
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
