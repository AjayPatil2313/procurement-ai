from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from apps.notifications.models import NotificationTemplate, NotificationRule


def build_card_body_html(
    greeting_name_expr,
    intro_text,
    card1_icon,
    card1_title,
    card1_items,
    card2_icon,
    card2_title,
    card2_items,
    next_step_desc,
    cta_label,
    cta_url_expr="{{ cta_url|default:'#' }}",
    secondary_label=None,
    secondary_url_expr="{{ dashboard_url|default:'#' }}",
    cta_bg="#0284c7",
):
    """
    Builds the standardized responsive 2-card + next-step HTML snippet matching
    the TradeConnect B2B reference design.
    This snippet occupies the dynamic body slot of the master layout.
    """
    c1_rows = ""
    for i in range(0, len(card1_items), 2):
        k1, v1 = card1_items[i]
        col1 = f"""
        <td width="50%" style="vertical-align: top; padding: 7px 12px 7px 0;">
            <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: uppercase; letter-spacing: 0.4px;">{k1}</span>
            <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 3px;">{v1}</span>
        </td>
        """
        if i + 1 < len(card1_items):
            k2, v2 = card1_items[i + 1]
            col2 = f"""
            <td width="50%" style="vertical-align: top; padding: 7px 0 7px 12px;">
                <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: uppercase; letter-spacing: 0.4px;">{k2}</span>
                <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 3px;">{v2}</span>
            </td>
            """
        else:
            col2 = '<td width="50%"></td>'
        c1_rows += f"<tr>{col1}{col2}</tr>\n"

    c2_rows = ""
    for i in range(0, len(card2_items), 2):
        k1, v1 = card2_items[i]
        col1 = f"""
        <td width="50%" style="vertical-align: top; padding: 7px 12px 7px 0;">
            <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: uppercase; letter-spacing: 0.4px;">{k1}</span>
            <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 3px;">{v1}</span>
        </td>
        """
        if i + 1 < len(card2_items):
            k2, v2 = card2_items[i + 1]
            col2 = f"""
            <td width="50%" style="vertical-align: top; padding: 7px 0 7px 12px;">
                <span style="font-size: 11px; font-weight: 600; color: #64748b; display: block; text-transform: uppercase; letter-spacing: 0.4px;">{k2}</span>
                <span style="font-size: 13px; font-weight: 700; color: #0f172a; display: block; margin-top: 3px;">{v2}</span>
            </td>
            """
        else:
            col2 = '<td width="50%"></td>'
        c2_rows += f"<tr>{col1}{col2}</tr>\n"

    secondary_btn_html = ""
    if secondary_label:
        secondary_btn_html = f"""
        <div style="margin-top: 10px;">
            <a href="{secondary_url_expr}" style="background-color: #ffffff; color: {cta_bg}; border: 1px solid {cta_bg}; padding: 10px 24px; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 12px; display: inline-block;">
                {secondary_label}
            </a>
        </div>
        """

    html = f"""
<div style="margin: 0 0 20px 0;">
  <p style="font-size: 18px; font-weight: 800; color: #0f172a; margin: 0 0 8px 0; line-height: 1.2;">
    Hello {greeting_name_expr},
  </p>
  <p style="font-size: 13px; color: #475569; line-height: 1.6; margin: 0;">
    {intro_text}
  </p>
</div>

<!-- Card 1: Primary Entity Details -->
<div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin-bottom: 16px;">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 12px; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px;">
    <tr>
      <td width="30" style="vertical-align: middle;">
        <div style="width: 26px; height: 26px; border-radius: 50%; background: #e0f2fe; color: #0284c7; text-align: center; line-height: 26px; font-size: 13px;">
          {card1_icon}
        </div>
      </td>
      <td style="vertical-align: middle; padding-left: 8px;">
        <span style="font-size: 13px; font-weight: 800; color: #0f172a;">{card1_title}</span>
      </td>
    </tr>
  </table>
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
    {c1_rows}
  </table>
</div>

<!-- Card 2: Secondary Scope / Specifications -->
<div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.02);">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 12px; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px;">
    <tr>
      <td width="30" style="vertical-align: middle;">
        <div style="width: 26px; height: 26px; border-radius: 50%; background: #e0f2fe; color: #0284c7; text-align: center; line-height: 26px; font-size: 13px;">
          {card2_icon}
        </div>
      </td>
      <td style="vertical-align: middle; padding-left: 8px;">
        <span style="font-size: 13px; font-weight: 800; color: #0f172a;">{card2_title}</span>
      </td>
    </tr>
  </table>
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
    {c2_rows}
  </table>
</div>

<!-- Card 3: Next Step Callout Box -->
<div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 22px 20px; text-align: center;">
  <div style="margin-bottom: 8px;">
    <span style="font-size: 16px; margin-right: 4px; vertical-align: middle;">&#9889;</span>
    <span style="font-size: 13px; font-weight: 800; color: #166534; vertical-align: middle;">Next Step</span>
  </div>
  <p style="font-size: 12px; color: #374151; line-height: 1.5; margin: 0 0 16px 0;">
    {next_step_desc}
  </p>
  <div>
    <a href="{cta_url_expr}" style="background-color: {cta_bg}; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 800; font-size: 13px; display: inline-block; box-shadow: 0 2px 6px rgba(2,132,199,0.25);">
      {cta_label} &rarr;
    </a>
  </div>
  {secondary_btn_html}
</div>
"""
    return html.strip()


DEFAULT_TEMPLATES = [
    # =========================================================================
    # SECTION 1: USER / COMPANY USER (6 Events)
    # =========================================================================

    # 1. Registration & Welcome Email
    {
        "event_key": "auth.welcome",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Registration & Welcome Email",
        "subject": "Welcome to TradeConnect, {{ user_name }}! Start Discovering Partners",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Valued Member' }}",
            intro_text="Welcome to TradeConnect — your B2B AI Procurement & Sales Platform. Your account has been initialized and is ready for immediate deployment.",
            card1_icon="&#128100;",
            card1_title="Account Overview",
            card1_items=[
                ("Registered Name", "{{ user_name|default:'Enterprise User' }}"),
                ("Company Workspace", "{{ company_name|default:'Your Enterprise' }}"),
                ("Primary Email", "{{ recipient_email|default:'user@company.com' }}"),
                ("Access Role", "{{ role_name|default:'Company Member' }}"),
            ],
            card2_icon="&#128640;",
            card2_title="Platform Capabilities",
            card2_items=[
                ("AI Supplier Discovery", "Active & Available"),
                ("Buyer Lead Matching", "Active & Available"),
                ("Digital Product Catalog", "Ready for Upload"),
                ("Support Access", "24/7 Enterprise Desk"),
            ],
            next_step_desc="Log in to complete your corporate profile, configure your discovery preferences, or post your first procurement requirement.",
            cta_label="Explore Your Dashboard",
            cta_url_expr="{{ cta_url|default:'/dashboard/' }}",
            secondary_label="Complete Profile",
            secondary_url_expr="{{ profile_url|default:'/company/profile/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Welcome to TradeConnect, {{ user_name }}! Your account is active. Log in at {{ cta_url }} to start discovering verified suppliers and buyers.",
        "variables": ["user_name", "company_name", "recipient_email", "role_name", "cta_url", "profile_url"],
        "rule": {
            "name": "User Registration & Welcome",
            "description": "Dispatched to newly registered users welcoming them and orienting them with onboarding guidance.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
            "is_mandatory_auth": False,
        },
    },

    # 2. Email Verification
    {
        "event_key": "auth.email_verification",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Email Verification",
        "subject": "Verify Your TradeConnect Email Address",
        "is_system": True,
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Valued User' }}",
            intro_text="Thank you for signing up on TradeConnect. Please verify your email address to authenticate your corporate credentials and activate all platform features.",
            card1_icon="&#9993;",
            card1_title="Verification Details",
            card1_items=[
                ("Email Address", "{{ recipient_email|default:'user@company.com' }}"),
                ("Link Validity", "{{ expires_in|default:'24 Hours' }}"),
                ("Request Type", "Email Address Confirmation"),
                ("Status", "Pending Verification"),
            ],
            card2_icon="&#128274;",
            card2_title="Security Verification",
            card2_items=[
                ("Authentication", "Cryptographic One-Time Token"),
                ("Identity Check", "Mandatory Security Step"),
            ],
            next_step_desc="Click below to verify your email. If you did not create this account, please disregard this email safely.",
            cta_label="Verify Email Address",
            cta_url_expr="{{ verification_url|default:'#' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Verify your email address for TradeConnect by visiting: {{ verification_url }}. Valid for {{ expires_in|default:'24 hours' }}.",
        "variables": ["user_name", "recipient_email", "verification_url", "expires_in"],
        "rule": {
            "name": "User Email Verification Token",
            "description": "Cryptographically secure one-time token link to authenticate user's email address upon registration.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.URGENT,
            "is_mandatory_auth": True,
            "allow_manual_send": False,
        },
    },

    # 3. Password Reset
    {
        "event_key": "auth.password_reset",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Password Reset",
        "subject": "Reset Your TradeConnect Account Password",
        "is_system": True,
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Enterprise User' }}",
            intro_text="We received a security request to reset your TradeConnect platform password. Click the button below to establish a new password for your account.",
            card1_icon="&#128274;",
            card1_title="Security Reset Request",
            card1_items=[
                ("Requested For", "{{ recipient_email|default:'user@company.com' }}"),
                ("Request Time", "{{ request_time|default:'Just now' }}"),
                ("Originating IP", "{{ request_ip|default:'Confidential' }}"),
                ("Token Validity", "{{ token_expiry|default:'24 Hours' }}"),
            ],
            card2_icon="&#9888;",
            card2_title="Safety Reminder",
            card2_items=[
                ("Account Integrity", "Never share this reset link"),
                ("Suspicious Activity?", "Contact security@tradeconnect.com"),
            ],
            next_step_desc="If you initiated this request, please proceed by clicking below. If you did not request a password reset, you can safely ignore this email.",
            cta_label="Reset Password",
            cta_url_expr="{{ reset_url|default:'#' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Password reset requested for TradeConnect. Click to reset your password: {{ reset_url }}. Valid for {{ token_expiry|default:'24 hours' }}.",
        "variables": ["user_name", "recipient_email", "request_time", "request_ip", "token_expiry", "reset_url"],
        "rule": {
            "name": "Password Reset Authentication",
            "description": "Cryptographically secure token link allowing users to reset their forgotten password. Mandatory security event.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.URGENT,
            "is_mandatory_auth": True,
            "allow_manual_send": False,
        },
    },

    # 4. Password Changed Confirmation
    {
        "event_key": "auth.password_changed",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Password Changed Confirmation",
        "subject": "Security Alert: Your Password Was Successfully Updated",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Enterprise User' }}",
            intro_text="This is an automated confirmation that the password for your TradeConnect account has been changed successfully.",
            card1_icon="&#128272;",
            card1_title="Change Details",
            card1_items=[
                ("Account Email", "{{ recipient_email|default:'user@company.com' }}"),
                ("Updated Timestamp", "{{ changed_at|default:'Just now' }}"),
                ("Originating IP", "{{ ip_address|default:'Recorded' }}"),
                ("Device / Agent", "{{ device_info|default:'Standard Web Browser' }}"),
            ],
            card2_icon="&#128737;",
            card2_title="Account Protection",
            card2_items=[
                ("Active Sessions", "Previous sessions invalidated"),
                ("Action Needed", "None if this was you"),
            ],
            next_step_desc="If you performed this change, no further action is required. If you did NOT change your password, lock your account immediately.",
            cta_label="Review Account Security",
            cta_url_expr="{{ cta_url|default:'/auth/forgot-password/' }}",
            secondary_label="Contact Support",
            secondary_url_expr="{{ support_url|default:'mailto:support@tradeconnect.com' }}",
            cta_bg="#059669",
        ),
        "text_content": "Security Notice: Your TradeConnect password was changed at {{ changed_at }}. If you did not make this change, contact support immediately: {{ support_url }}.",
        "variables": ["user_name", "recipient_email", "changed_at", "ip_address", "device_info", "cta_url", "support_url"],
        "rule": {
            "name": "Password Changed Confirmation Notice",
            "description": "Security confirmation dispatched immediately upon user password modification.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
            "is_mandatory_auth": False,
        },
    },

    # 5. Account Activation / Deactivation
    {
        "event_key": "user.activation_deactivation",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Account Activation / Deactivation",
        "subject": "Account Status Update: Your TradeConnect Access is {{ status_state }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Valued Member' }}",
            intro_text="Your TradeConnect team account status within {{ company_name }} has been modified by an administrator.",
            card1_icon="&#128100;",
            card1_title="Status Overview",
            card1_items=[
                ("Company Workspace", "{{ company_name|default:'Your Enterprise' }}"),
                ("Current Status", "{{ status_state|default:'Updated' }}"),
                ("Effective Date", "{{ action_date|default:'Today' }}"),
                ("Authorized By", "{{ operator_name|default:'Company Administrator' }}"),
            ],
            card2_icon="&#128221;",
            card2_title="Administrative Remark",
            card2_items=[
                ("Reason / Remark", "{{ reason|default:'Administrative review and routine access update' }}"),
                ("Next Action", "Contact your workspace admin for inquiries"),
            ],
            next_step_desc="If your account is active, you may log in and continue your workflows. For deactivations, all session tokens have been cleared.",
            cta_label="Access Workspace",
            cta_url_expr="{{ cta_url|default:'/dashboard/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Your TradeConnect account for {{ company_name }} has been {{ status_state }}. Reason: {{ reason }}. Date: {{ action_date }}.",
        "variables": ["user_name", "company_name", "status_state", "action_date", "operator_name", "reason", "cta_url"],
        "rule": {
            "name": "User Account Activation / Deactivation",
            "description": "Dispatched to user when their company account is activated or deactivated by an admin.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.NORMAL,
            "is_mandatory_auth": False,
        },
    },

    # 6. Subscription Expiry Reminder (User)
    {
        "event_key": "billing.subscription_expiry_user",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Subscription Expiry Reminder (Team User)",
        "subject": "Notice: {{ company_name }}'s Subscription Expires in {{ days_left }} Days",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Team Member' }}",
            intro_text="Your organization's TradeConnect platform subscription is scheduled to expire in {{ days_left }} days. Please inform your company billing manager to ensure uninterrupted access.",
            card1_icon="&#128337;",
            card1_title="Subscription Details",
            card1_items=[
                ("Current Plan", "{{ plan_name|default:'Enterprise Growth' }}"),
                ("Expiration Date", "{{ expiry_date|default:'Approaching' }}"),
                ("Days Remaining", "{{ days_left|default:'5' }} Days"),
                ("Company", "{{ company_name|default:'Your Enterprise' }}"),
            ],
            card2_icon="&#9888;",
            card2_title="Impact on Features",
            card2_items=[
                ("AI Search Access", "Suspended upon expiry"),
                ("Buyer Inquiries", "Archived until renewed"),
            ],
            next_step_desc="To maintain continuous access to supplier and buyer discovery, check with your company administrator or billing contact.",
            cta_label="View Plan Status",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#d97706",
        ),
        "text_content": "Reminder: {{ company_name }} subscription expires in {{ days_left }} days on {{ expiry_date }}. Contact your billing administrator to renew.",
        "variables": ["user_name", "company_name", "plan_name", "expiry_date", "days_left", "cta_url"],
        "rule": {
            "name": "Subscription Expiry Reminder (User Level)",
            "description": "Advance reminder sent to team members alerting them that the company subscription is approaching expiration.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.NORMAL,
            "reminder_days_before": 5,
        },
    },

    # =========================================================================
    # SECTION 2: COMPANY ADMIN (6 Events)
    # =========================================================================

    # 7. New Company User Added
    {
        "event_key": "company.user_added",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "New Company User Added",
        "subject": "New Team Member Added: {{ new_user_name }} joined {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Admin' }}",
            intro_text="A new team member has been successfully added to your corporate workspace on TradeConnect.",
            card1_icon="&#128101;",
            card1_title="New Member Details",
            card1_items=[
                ("Full Name", "{{ new_user_name|default:'New Team Member' }}"),
                ("Email Address", "{{ new_user_email|default:'member@company.com' }}"),
                ("Assigned Role", "{{ role|default:'User' }}"),
                ("Added On", "{{ added_at|default:'Today' }}"),
            ],
            card2_icon="&#128737;",
            card2_title="Team Capacity & Access",
            card2_items=[
                ("Active Members", "{{ total_members|default:'Active' }}"),
                ("Access Permissions", "{{ role|default:'Standard Workspace Permissions' }}"),
            ],
            next_step_desc="You can modify user roles, adjust permissions, or deactivate seats anytime from the Team Management dashboard.",
            cta_label="Manage Team Members",
            cta_url_expr="{{ cta_url|default:'/company/team/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "New member {{ new_user_name }} ({{ new_user_email }}) was added to {{ company_name }} as {{ role }}. Manage team at: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "new_user_name", "new_user_email", "role", "added_at", "total_members", "cta_url"],
        "rule": {
            "name": "Notify Company Admin on New User Addition",
            "description": "Alerts company administrators when a new user is invited or added to their organization workspace.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # 8. Company Verification Approved / Rejected
    {
        "event_key": "company.verification_decision",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "Company Verification Approved / Rejected",
        "subject": "Corporate Verification Decision: {{ decision_status }} for {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Administrator' }}",
            intro_text="The Platform Super Administrator has completed the verification review of your corporate entity on TradeConnect.",
            card1_icon="&#128737;",
            card1_title="Verification Decision",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Your Enterprise' }}"),
                ("Review Decision", "{{ decision_status|default:'Approved' }}"),
                ("Verified Badge", "{{ verified_badge|default:'Active' }}"),
                ("Decision Date", "{{ action_date|default:'Today' }}"),
            ],
            card2_icon="&#128221;",
            card2_title="Reviewer Feedback",
            card2_items=[
                ("Official Remarks", "{{ reviewer_notes|default:'All documentation authenticated successfully.' }}"),
                ("Trust Score", "{{ trust_status|default:'Tier 1 Verified' }}"),
            ],
            next_step_desc="Verified organizations receive higher search placement, verified badges on all quotations, and priority discovery exposure.",
            cta_label="View Verification Status",
            cta_url_expr="{{ cta_url|default:'/company/profile/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Verification decision for {{ company_name }}: {{ decision_status }}. Remarks: {{ reviewer_notes }}. Details: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "decision_status", "verified_badge", "action_date", "reviewer_notes", "trust_status", "cta_url"],
        "rule": {
            "name": "Company Verification Decision Alert",
            "description": "Official notice sent to company admins when their corporate verification is approved or rejected by super admins.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # 9. Company Credits Running Low
    {
        "event_key": "billing.credits_low",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Company Credits Running Low",
        "subject": "Low Credit Warning: {{ company_name }} Has {{ current_credits }} AI Credits Left",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Billing Administrator' }}",
            intro_text="Your organization's AI search and discovery credits have fallen below the configured alert threshold. Top up now to prevent disruption.",
            card1_icon="&#128267;",
            card1_title="Credit Pool Status",
            card1_items=[
                ("Available Credits", "{{ current_credits|default:'25' }} Credits"),
                ("Threshold Cutoff", "{{ threshold_credits|default:'50' }} Credits"),
                ("Estimated Runway", "{{ runway_estimate|default:'1-2 Days of Active Searches' }}"),
                ("Company", "{{ company_name|default:'Your Enterprise' }}"),
            ],
            card2_icon="&#128640;",
            card2_title="Impact on Active Searches",
            card2_items=[
                ("Autonomous Search", "Subject to throttling"),
                ("Contact Unlocks", "Blocked when balance is 0"),
            ],
            next_step_desc="Top up your credit balance or upgrade your monthly subscription package to maintain continuous AI supplier discovery.",
            cta_label="Recharge Credits Now",
            cta_url_expr="{{ topup_url|default:'/company/billing/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "Low Credit Alert for {{ company_name }}: Only {{ current_credits }} AI credits remaining (Threshold: {{ threshold_credits }}). Recharge at: {{ topup_url }}.",
        "variables": ["recipient_name", "company_name", "current_credits", "threshold_credits", "runway_estimate", "topup_url"],
        "rule": {
            "name": "Company Credits Running Low Alert",
            "description": "Triggered when available AI credits cross below the configured threshold cutoff. Cooldown prevents duplicate spam.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
            "threshold_value": Decimal("50.00"),
            "cooldown_hours": 24,
        },
    },

    # 10. Subscription Expiring / Expired (Admin)
    {
        "event_key": "billing.subscription_expiring_admin",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Subscription Expiring / Expired (Admin)",
        "subject": "Action Required: {{ company_name }} Subscription Renews/Expires on {{ expiry_date }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Administrator' }}",
            intro_text="Your TradeConnect corporate subscription is due for renewal or expiration in {{ days_left }} days. Please review your billing details.",
            card1_icon="&#128197;",
            card1_title="Subscription Details",
            card1_items=[
                ("Current Plan", "{{ plan_name|default:'Enterprise Pro Tier' }}"),
                ("Expiration / Renewal", "{{ expiry_date|default:'Upcoming' }}"),
                ("Auto-Pay Status", "{{ auto_renew_status|default:'Active' }}"),
                ("Renewal Amount", "₹{{ renewal_amount|default:'15,000.00' }}"),
            ],
            card2_icon="&#128179;",
            card2_title="Wallet & Payment Status",
            card2_items=[
                ("Company Wallet", "₹{{ wallet_balance|default:'0.00' }}"),
                ("Payment Readiness", "{{ payment_readiness|default:'Review Balance' }}"),
            ],
            next_step_desc="Ensure sufficient wallet balance is maintained for automatic settlement or renew manually to prevent operational interruptions.",
            cta_label="Manage Subscription & Billing",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#d97706",
        ),
        "text_content": "{{ company_name }} subscription expires/renews on {{ expiry_date }} ({{ days_left }} days left). Plan: {{ plan_name }}. Amount: ₹{{ renewal_amount }}. Manage at: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "plan_name", "expiry_date", "days_left", "auto_renew_status", "renewal_amount", "wallet_balance", "payment_readiness", "cta_url"],
        "rule": {
            "name": "Subscription Expiring / Expired (Admin Notice)",
            "description": "Advance notice to company admins regarding renewal dates, wallet auto-pay readiness, or expiration.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
            "reminder_days_before": 7,
        },
    },

    # 11. Payment Failed
    {
        "event_key": "billing.payment_failed",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Payment Failed Notice",
        "subject": "Payment Failed: Transaction of ₹{{ amount }} for {{ company_name }} Unsuccessful",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Billing Contact' }}",
            intro_text="We were unable to process a recent payment or auto-renewal charge for your TradeConnect corporate account.",
            card1_icon="&#9888;",
            card1_title="Failed Transaction",
            card1_items=[
                ("Attempted Amount", "₹{{ amount|default:'10,000.00' }}"),
                ("Invoice / Order ID", "{{ invoice_id|default:'INV-FAILED-001' }}"),
                ("Failure Reason", "{{ failure_reason|default:'Insufficient wallet funds or card declined' }}"),
                ("Attempt Time", "{{ attempt_time|default:'Just now' }}"),
            ],
            card2_icon="&#128179;",
            card2_title="Account Status",
            card2_items=[
                ("Grace Period", "3 Business Days"),
                ("Action Required", "Update payment method or recharge wallet"),
            ],
            next_step_desc="Please review your payment method or recharge your company wallet to retry this payment and avoid service interruption.",
            cta_label="Retry Payment Now",
            cta_url_expr="{{ retry_url|default:'/company/billing/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "Payment failed for {{ company_name }}: ₹{{ amount }} on {{ attempt_time }}. Reason: {{ failure_reason }}. Retry now at: {{ retry_url }}.",
        "variables": ["recipient_name", "company_name", "amount", "invoice_id", "failure_reason", "attempt_time", "retry_url"],
        "rule": {
            "name": "Payment Failed Notification",
            "description": "Dispatched to company billing contacts when a renewal, top-up, or card payment fails.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # 12. Company Profile / Verification Updated
    {
        "event_key": "company.profile_updated",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "Company Profile / Verification Updated",
        "subject": "Security Notice: Profile & Verification Details Updated for {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Administrator' }}",
            intro_text="Critical corporate information or tax verification details for your company were recently updated.",
            card1_icon="&#128221;",
            card1_title="Update Summary",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Your Enterprise' }}"),
                ("Updated By", "{{ updated_by|default:'Administrator' }}"),
                ("Modified Fields", "{{ updated_fields|default:'Legal name, GST/Tax ID, or Address' }}"),
                ("Timestamp", "{{ update_time|default:'Just now' }}"),
            ],
            card2_icon="&#128274;",
            card2_title="Security Verification",
            card2_items=[
                ("Re-Verification", "Automatic re-check initiated if tax ID changed"),
                ("Audit Record", "Recorded in platform compliance audit log"),
            ],
            next_step_desc="Review the changes from your company profile page. If you did not authorize these updates, contact TradeConnect support immediately.",
            cta_label="Review Company Profile",
            cta_url_expr="{{ cta_url|default:'/company/profile/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Profile updated for {{ company_name }} by {{ updated_by }} at {{ update_time }}. Modified: {{ updated_fields }}. View: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "updated_by", "updated_fields", "update_time", "cta_url"],
        "rule": {
            "name": "Company Profile / Verification Updated Alert",
            "description": "Security notification dispatched to company admins whenever critical corporate data, tax IDs, or addresses are modified.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # 12B. Company Account Suspended Notice
    {
        "event_key": "company.suspended",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "Company Account Suspended Notice",
        "subject": "CRITICAL NOTICE: Corporate Account Suspended — {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Administrator' }}",
            intro_text="This official notice is to inform you that your company account on TradeConnect has been suspended by the Platform Administrator effective immediately.",
            card1_icon="&#9888;",
            card1_title="Suspension Notice",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Your Enterprise' }}"),
                ("Account Status", "Suspended (Access Revoked)"),
                ("Effective Time", "{{ timestamp|default:'Immediately' }}"),
                ("Security Action", "All User Sessions Terminated"),
            ],
            card2_icon="&#128221;",
            card2_title="Reason & Administrative Remark",
            card2_items=[
                ("Suspension Reason", "{{ reason|default:'Platform governance policy enforcement' }}"),
                ("Appeal / Review", "Contact Platform Administration"),
            ],
            next_step_desc="While suspended, all team members belonging to this organization have been logged out and cannot perform any procurement or sales operations. To appeal, contact the support desk.",
            cta_label="Contact Support Desk",
            cta_url_expr="{{ cta_url|default:'/support/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "CRITICAL NOTICE: Corporate Account Suspended - {{ company_name }}\n\nReason: {{ reason }}\nTimestamp: {{ timestamp }}\n\nAll active user sessions have been terminated. Contact platform administration for questions.",
        "variables": ["recipient_name", "company_name", "reason", "timestamp", "cta_url"],
        "rule": {
            "name": "Notify Company Admin on Account Suspension",
            "description": "Critical security notice sent immediately to company admins when their organization is suspended by Super Admin.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # 12C. Company Account Reactivated Notice
    {
        "event_key": "company.reactivated",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "Company Account Reactivated Notice",
        "subject": "Account Restored: Corporate Account Reactivated — {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Company Administrator' }}",
            intro_text="We are pleased to inform you that your company account on TradeConnect has been reactivated. Full access to platform features, procurement tools, and vendor operations has been restored.",
            card1_icon="&#10004;",
            card1_title="Account Status",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Your Enterprise' }}"),
                ("Account Status", "Active (Access Restored)"),
                ("Restoration Time", "{{ timestamp|default:'Today' }}"),
                ("Team Access", "Login Enabled"),
            ],
            card2_icon="&#128221;",
            card2_title="Administrator Remark",
            card2_items=[
                ("Administrative Remark", "{{ reason|default:'Account restored by Platform Administration.' }}"),
                ("Catalog & RFQs", "Fully Accessible"),
            ],
            next_step_desc="All authorized users belonging to your company may now log in to the platform and resume their day-to-day procurement, quotation, and catalog management activities.",
            cta_label="Log In to Company Dashboard",
            cta_url_expr="{{ cta_url|default:'/company/dashboard/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Account Restored: Corporate Account Reactivated - {{ company_name }}\n\nRemark: {{ reason }}\nTimestamp: {{ timestamp }}\n\nYour team members may now log in to the platform.",
        "variables": ["recipient_name", "company_name", "reason", "timestamp", "cta_url"],
        "rule": {
            "name": "Notify Company Admin on Account Reactivation",
            "description": "Notice dispatched to company admins when their organization is restored/reactivated by Super Admin.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # =========================================================================
    # SECTION 3: SUPER ADMIN (9 Events)
    # =========================================================================

    # 13. New Company Registration
    {
        "event_key": "admin.new_company",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: New Company Registration",
        "subject": "Platform Alert: New Company Registered - {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Super Administrator",
            intro_text="A new enterprise entity has completed registration and created a corporate workspace on TradeConnect.",
            card1_icon="&#127970;",
            card1_title="Company Overview",
            card1_items=[
                ("Company Name", "{{ company_name|default:'Apex Enterprise' }}"),
                ("Company Type", "{{ company_type|default:'Buyer & Seller' }}"),
                ("Administrator", "{{ admin_name|default:'Corporate Lead' }}"),
                ("Contact Email", "{{ admin_email|default:'lead@apex.com' }}"),
                ("Location", "{{ location|default:'Mumbai, India' }}"),
                ("Registered At", "{{ registered_at|default:'Just now' }}"),
            ],
            card2_icon="&#128269;",
            card2_title="Administrative Review",
            card2_items=[
                ("KYC Verification", "Pending Review"),
                ("Initial Wallet", "₹0.00 (Ready for allocation)"),
            ],
            next_step_desc="Inspect corporate credentials, verify tax/GST details, and approve verification badge if requested.",
            cta_label="View Company in Admin Panel",
            cta_url_expr="{{ cta_url|default:'/admin-panel/companies/' }}",
            cta_bg="#4f46e5",
        ),
        "text_content": "New company registered on TradeConnect: {{ company_name }} ({{ company_type }}). Admin: {{ admin_name }} ({{ admin_email }}). View: {{ cta_url }}.",
        "variables": ["company_name", "company_type", "admin_name", "admin_email", "location", "registered_at", "cta_url"],
        "rule": {
            "name": "Super Admin: New Company Registration",
            "description": "Alerts Super Admins immediately when a new business entity registers on the platform.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # 14. Company Verification Pending
    {
        "event_key": "admin.verification_pending",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Company Verification Pending",
        "subject": "KYC Review Required: Verification Pending for {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Super Administrator",
            intro_text="An enterprise has submitted official business documents requesting platform verification and trust badge approval.",
            card1_icon="&#128737;",
            card1_title="Verification Request",
            card1_items=[
                ("Company Name", "{{ company_name|default:'Enterprise Corp' }}"),
                ("GST / Tax ID", "{{ gst_vat_no|default:'Document Uploaded' }}"),
                ("Documents Submitted", "{{ document_count|default:'GST, Incorporation, MSME' }}"),
                ("Submitted At", "{{ submitted_at|default:'Today' }}"),
            ],
            card2_icon="&#9889;",
            card2_title="SLA Target",
            card2_items=[
                ("Verification SLA", "24 Business Hours"),
                ("Badge Type", "Verified B2B Enterprise"),
            ],
            next_step_desc="Open the Admin Review portal to cross-check regulatory filings against corporate registries and grant the Verified badge.",
            cta_label="Review Verification Dossier",
            cta_url_expr="{{ review_url|default:'/admin-panel/companies/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Verification pending for {{ company_name }}. Documents: {{ document_count }}. Review in Super Admin: {{ review_url }}.",
        "variables": ["company_name", "gst_vat_no", "document_count", "submitted_at", "review_url"],
        "rule": {
            "name": "Super Admin: Company Verification Pending",
            "description": "Notifies Super Admins when an enterprise submits KYC verification documents for review.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # 15. Company Verification Approved / Rejected Audit
    {
        "event_key": "admin.verification_decision",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Audit: Company Verification Decision",
        "subject": "Audit Trail: Verification Decision [{{ decision }}] for {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Compliance Desk",
            intro_text="An audited verification decision was recorded by an authorized administrator on TradeConnect.",
            card1_icon="&#128221;",
            card1_title="Audit Details",
            card1_items=[
                ("Target Entity", "{{ company_name|default:'Enterprise Corp' }}"),
                ("Decision Taken", "{{ decision|default:'Approved / Granted' }}"),
                ("Admin Operator", "{{ admin_operator|default:'Super Admin' }}"),
                ("Effective Timestamp", "{{ decision_date|default:'Today' }}"),
            ],
            card2_icon="&#128737;",
            card2_title="Compliance Impact",
            card2_items=[
                ("Badge State", "Updated across public catalogs"),
                ("Audit Log ID", "{{ audit_id|default:'AUD-VERIF-001' }}"),
            ],
            next_step_desc="Review complete compliance audit trails and decision logs in the platform governance center.",
            cta_label="View Audit Trail",
            cta_url_expr="{{ audit_url|default:'/admin-panel/audit-logs/' }}",
            cta_bg="#334155",
        ),
        "text_content": "Audit: Verification {{ decision }} for {{ company_name }} by {{ admin_operator }} on {{ decision_date }}. Audit URL: {{ audit_url }}.",
        "variables": ["company_name", "decision", "admin_operator", "decision_date", "audit_id", "audit_url"],
        "rule": {
            "name": "Super Admin: Verification Decision Audit Copy",
            "description": "Compliance audit notification dispatched when a super admin approves, rejects, or revokes a company verification badge.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # 16. New User Registration
    {
        "event_key": "admin.new_user",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: New User Registration",
        "subject": "Platform Notice: New User Registered - {{ user_name }} ({{ user_email }})",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Super Administrator",
            intro_text="A new individual user account has registered or joined a corporate workspace on TradeConnect.",
            card1_icon="&#128100;",
            card1_title="User Details",
            card1_items=[
                ("Full Name", "{{ user_name|default:'Enterprise User' }}"),
                ("Email Address", "{{ user_email|default:'user@company.com' }}"),
                ("Company Workspace", "{{ company_name|default:'Enterprise Corp' }}"),
                ("Registered At", "{{ registered_at|default:'Just now' }}"),
            ],
            card2_icon="&#128737;",
            card2_title="Account Status",
            card2_items=[
                ("Email Verified", "{{ email_verified|default:'Pending' }}"),
                ("Status", "Active"),
            ],
            next_step_desc="Monitor platform adoption and user provisioning across enterprise workspaces.",
            cta_label="View Users in Admin Panel",
            cta_url_expr="{{ cta_url|default:'/admin-panel/users/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "New user registered on TradeConnect: {{ user_name }} ({{ user_email }}) in {{ company_name }}. Registered at {{ registered_at }}.",
        "variables": ["user_name", "user_email", "company_name", "registered_at", "email_verified", "cta_url"],
        "rule": {
            "name": "Super Admin: New User Registration Alert",
            "description": "Operational notification to Super Admins when any individual user joins an organization or signs up.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.LOW,
        },
    },

    # 17. Company Suspended / Reactivated
    {
        "event_key": "admin.company_suspended",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Company Suspended / Reactivated",
        "subject": "Governance Alert: Company {{ company_name }} Has Been {{ action_type }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Super Admin Governance Desk",
            intro_text="An administrative governance action was executed changing the active status of an enterprise account on TradeConnect.",
            card1_icon="&#9888;",
            card1_title="Governance Action",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Target Enterprise' }}"),
                ("Status Action", "{{ action_type|default:'Suspended' }}"),
                ("Operator", "{{ operator_name|default:'Super Admin' }}"),
                ("Action Timestamp", "{{ timestamp|default:'Just now' }}"),
            ],
            card2_icon="&#128221;",
            card2_title="Mandatory Remark & Impact",
            card2_items=[
                ("Audited Reason", "{{ remark|default:'Policy violation or compliance audit' }}"),
                ("User Sessions", "{{ session_status|default:'All active sessions terminated' }}"),
            ],
            next_step_desc="Review the company details, audit logs, and reactivate the workspace if compliance criteria are satisfied.",
            cta_label="Inspect Company Record",
            cta_url_expr="{{ cta_url|default:'/admin-panel/companies/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "Company {{ company_name }} was {{ action_type }} by {{ operator_name }} at {{ timestamp }}. Reason: {{ remark }}.",
        "variables": ["company_name", "action_type", "operator_name", "timestamp", "remark", "session_status", "cta_url"],
        "rule": {
            "name": "Super Admin: Company Suspended / Reactivated Alert",
            "description": "Audit alert dispatched when a company is suspended or reactivated with recorded mandatory remarks.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # 18. Subscription Purchased / Cancelled
    {
        "event_key": "admin.subscription_change",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Subscription Purchased / Cancelled",
        "subject": "Revenue Alert: Subscription {{ action_type }} - {{ company_name }} ({{ plan_name }})",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Finance Desk",
            intro_text="A corporate subscription transaction (Purchase, Upgrade, Downgrade, or Cancellation) was processed on the platform.",
            card1_icon="&#128176;",
            card1_title="Subscription Event",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Apex Corp' }}"),
                ("Action Executed", "{{ action_type|default:'Purchased' }}"),
                ("Subscription Plan", "{{ plan_name|default:'Pro Growth Tier' }}"),
                ("Billing Cycle", "{{ billing_cycle|default:'Monthly' }}"),
                ("Transaction Amount", "₹{{ amount|default:'10,000.00' }}"),
                ("Effective Date", "{{ effective_date|default:'Today' }}"),
            ],
            card2_icon="&#128202;",
            card2_title="Financial Summary",
            card2_items=[
                ("Included Credits", "{{ credits_allocated|default:'500 AI Credits' }}"),
                ("Payment Method", "{{ payment_channel|default:'Razorpay Corporate / Wallet' }}"),
            ],
            next_step_desc="Review transaction invoices, ledger entries, and MRR metrics in the Super Admin revenue dashboard.",
            cta_label="View Billing Analytics",
            cta_url_expr="{{ cta_url|default:'/admin-panel/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Subscription {{ action_type }} for {{ company_name }}: Plan {{ plan_name }} (₹{{ amount }}). Effective {{ effective_date }}. View: {{ cta_url }}.",
        "variables": ["company_name", "action_type", "plan_name", "billing_cycle", "amount", "effective_date", "credits_allocated", "payment_channel", "cta_url"],
        "rule": {
            "name": "Super Admin: Subscription Change Alert",
            "description": "Financial alert to Super Admins when a company purchases, upgrades, or cancels a subscription plan.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # 19. Payment Failure / Billing Issue
    {
        "event_key": "admin.payment_issue",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Payment Failure / Billing Issue",
        "subject": "CRITICAL Billing Alert: Payment Failure for {{ company_name }} (₹{{ amount }})",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Super Administrator",
            intro_text="An automated billing run or customer payment transaction failed, requiring administrative attention.",
            card1_icon="&#9888;",
            card1_title="Failure Diagnostic",
            card1_items=[
                ("Company Entity", "{{ company_name|default:'Enterprise Corp' }}"),
                ("Attempted Amount", "₹{{ amount|default:'15,000.00' }}"),
                ("Gateway Reference", "{{ gateway_ref|default:'GW-ERR-9812' }}"),
                ("Error Code", "{{ error_code|default:'CARD_DECLINED_INSUFFICIENT_FUNDS' }}"),
                ("Error Message", "{{ error_message|default:'Transaction declined by issuing bank' }}"),
            ],
            card2_icon="&#128179;",
            card2_title="Account Health",
            card2_items=[
                ("Auto-Pay Status", "Suspended pending settlement"),
                ("Retry Count", "{{ retry_count|default:'Attempt 1 of 3' }}"),
            ],
            next_step_desc="Check gateway webhooks, inspect payment logs, and reach out to the customer billing team if necessary.",
            cta_label="Inspect Payment in Admin Panel",
            cta_url_expr="{{ cta_url|default:'/admin-panel/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "CRITICAL: Payment failure for {{ company_name }}: ₹{{ amount }}. Error: {{ error_message }} (Code: {{ error_code }}). Inspect: {{ cta_url }}.",
        "variables": ["company_name", "amount", "gateway_ref", "error_code", "error_message", "retry_count", "cta_url"],
        "rule": {
            "name": "Super Admin: Payment Failure / Billing Issue Alert",
            "description": "Escalated billing alert when customer payments or subscription auto-renewals fail.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.BOTH,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # 20. Email Delivery Failure
    {
        "event_key": "admin.email_delivery_failure",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Email Delivery Failure",
        "subject": "DELIVERY ALERT: Email Transmission Failed for {{ recipient_email }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Email Operations Desk",
            intro_text="An automated transactional notification failed all retry attempts and has been marked as permanently failed in the outbox.",
            card1_icon="&#9888;",
            card1_title="Failure Summary",
            card1_items=[
                ("Target Recipient", "{{ recipient_email|default:'lead@enterprise.com' }}"),
                ("Event Key", "{{ failed_event|default:'Operational Dispatch' }}"),
                ("Email Subject", "{{ subject|default:'Transactional Notification' }}"),
                ("Attempts Exhausted", "{{ attempts_count|default:'3' }} of 3 Attempts"),
                ("Log ID", "#{{ log_id|default:'1001' }}"),
            ],
            card2_icon="&#128736;",
            card2_title="SMTP Diagnostic",
            card2_items=[
                ("Error Message", "{{ error_message|default:'SMTP connection timed out or recipient rejected' }}"),
                ("Recommended Fix", "Inspect SMTP credentials and verify recipient domain"),
            ],
            next_step_desc="Open the Email Delivery Outbox to inspect the full sanitized traceback, retry manually, or reconfigure active SMTP settings.",
            cta_label="Inspect Failed Log",
            cta_url_expr="{{ cta_url|default:'/admin-panel/email/logs/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "Email Delivery Failure for {{ recipient_email }} on event {{ failed_event }}. Attempts exhausted ({{ attempts_count }}). Error: {{ error_message }}. View: {{ cta_url }}.",
        "variables": ["recipient_email", "failed_event", "subject", "attempts_count", "log_id", "error_message", "cta_url"],
        "rule": {
            "name": "Super Admin: Email Delivery Failure Alert",
            "description": "Technical alert dispatched when a transactional notification exhausts all delivery retries.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # 21. Repeated Authentication / Security Alerts
    {
        "event_key": "admin.security_alert",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: Authentication & Security Incident",
        "subject": "SECURITY INCIDENT: {{ alert_title }} [{{ incident_code|default:'SEC-WARN-01' }}]",
        "html_content": build_card_body_html(
            greeting_name_expr="Platform Security Officer",
            intro_text="Anomalous or repeated authentication failures, brute-force attempts, or suspicious activity was detected on TradeConnect.",
            card1_icon="&#128680;",
            card1_title="Incident Telemetry",
            card1_items=[
                ("Incident Code", "{{ incident_code|default:'SEC-AUTH-091' }}"),
                ("Alert Summary", "{{ alert_title|default:'Repeated Failed Login Attempts' }}"),
                ("Target Account", "{{ target_account|default:'admin@procurement-ai.com' }}"),
                ("Source IP", "{{ source_ip|default:'103.21.144.10' }}"),
                ("Detection Time", "{{ incident_time|default:'Just now' }}"),
            ],
            card2_icon="&#128737;",
            card2_title="Mitigation Status",
            card2_items=[
                ("Rate Limiting", "Enforced on source IP"),
                ("Account Status", "{{ account_state|default:'Temporary Lock Engaged' }}"),
            ],
            next_step_desc="Inspect security audit trails, verify origin telemetry, and escalate to incident response if persistent attack patterns emerge.",
            cta_label="View Security Audit Logs",
            cta_url_expr="{{ cta_url|default:'/admin-panel/audit-logs/' }}",
            cta_bg="#991b1b",
        ),
        "text_content": "SECURITY INCIDENT: {{ alert_title }} for {{ target_account }} from IP {{ source_ip }} at {{ incident_time }}. Inspect: {{ cta_url }}.",
        "variables": ["alert_title", "incident_code", "target_account", "source_ip", "incident_time", "account_state", "cta_url"],
        "rule": {
            "name": "Super Admin: Security Alert & Threat Policy",
            "description": "Dispatched to Super Admins upon repeated authentication failures, token abuse, or security anomalies.",
            "target_audience": NotificationRule.TargetAudience.SUPER_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # =========================================================================
    # PRESERVED DOMAIN & COMPATIBILITY EVENTS
    # =========================================================================

    # Inquiry Received
    {
        "event_key": "seller.inquiry_received",
        "category": NotificationTemplate.Category.SELLER,
        "name": "New Inquiry Received (Vendor)",
        "subject": "New Inquiry Received: {{ buyer_company }} for {{ product_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Seller Team' }}",
            intro_text="A verified buyer has shown direct interest in your catalog product and submitted an inquiry.",
            card1_icon="&#128100;",
            card1_title="Buyer Details",
            card1_items=[
                ("Company Name", "{{ buyer_company|default:'ABC Industries' }}"),
                ("Phone", "{{ buyer_phone|default:'+91 98765 43210' }}"),
                ("Contact Person", "{{ contact_person|default:'Purchasing Lead' }}"),
                ("Location", "{{ buyer_location|default:'Ahmedabad, Gujarat' }}"),
            ],
            card2_icon="&#128230;",
            card2_title="Product Requirement",
            card2_items=[
                ("Product Name", "{{ product_name|default:'PP Woven Bag' }}"),
                ("Quantity Required", "{{ quantity|default:'10,000 Bags' }}"),
                ("Specification", "{{ specification|default:'Standard Specifications' }}"),
            ],
            next_step_desc="View full inquiry details, submit an official price quotation, or get in touch directly.",
            cta_label="View Inquiry",
            cta_url_expr="{{ cta_url|default:'/sales/inquiries/' }}",
            secondary_label="Go to Vendor Dashboard",
            secondary_url_expr="{{ dashboard_url|default:'/dashboard/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "New Inquiry from {{ buyer_company }} for {{ product_name }}. View at: {{ cta_url }}.",
        "variables": ["recipient_name", "buyer_company", "contact_person", "buyer_phone", "buyer_location", "product_name", "quantity", "specification", "cta_url", "dashboard_url"],
        "rule": {
            "name": "Notify Vendor on New Inbound Inquiry",
            "description": "Alerts vendor when a buyer submits a direct RFQ or product inquiry.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.SALES,
            "company_type": NotificationRule.CompanyTypeScope.SELLER,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Matching Suppliers Found
    {
        "event_key": "buyer.matching_suppliers_found",
        "category": NotificationTemplate.Category.BUYER,
        "name": "Matching Suppliers Found (Buyer)",
        "subject": "Matching Suppliers Found: {{ count }} Verified Vendors for {{ requirement_title }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Procurement Lead' }}",
            intro_text="Our AI Discovery Engine has identified verified suppliers matching your sourcing requirement.",
            card1_icon="&#128269;",
            card1_title="Sourcing Requirement",
            card1_items=[
                ("Requirement", "{{ requirement_title|default:'Industrial Supplies' }}"),
                ("Matched Suppliers", "{{ count|default:'5' }} Verified Vendors"),
            ],
            card2_icon="&#127970;",
            card2_title="Top Matched Vendor",
            card2_items=[
                ("Top Supplier", "{{ top_supplier|default:'Apex Engineering Works' }}"),
                ("Verification", "{{ verification_status|default:'GST & MSME Verified' }}"),
            ],
            next_step_desc="Review matched vendor profiles, view capability scorecards, and broadcast RFQs.",
            cta_label="Review Suppliers & Send RFQ",
            cta_url_expr="{{ cta_url|default:'/procurement/find-suppliers/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Found {{ count }} matching suppliers for {{ requirement_title }}. Review at: {{ cta_url }}.",
        "variables": ["recipient_name", "requirement_title", "count", "top_supplier", "verification_status", "cta_url"],
        "rule": {
            "name": "Notify Buyer When AI Finds Matching Suppliers",
            "description": "Dispatched to procurement team when autonomous discovery identifies matching vendors.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.PROCUREMENT,
            "company_type": NotificationRule.CompanyTypeScope.BUYER,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Wallet Recharge Success
    {
        "event_key": "billing.wallet_recharge_success",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Wallet Recharge Successful",
        "subject": "Wallet Recharge Successful: ₹{{ amount }} Added to {{ company_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Manager' }}",
            intro_text="Your payment has been confirmed and credited to your company wallet.",
            card1_icon="&#10004;",
            card1_title="Transaction Summary",
            card1_items=[
                ("Recharge Amount", "₹{{ amount|default:'10,000.00' }}"),
                ("Transaction Ref", "{{ transaction_ref|default:'TXN-90218' }}"),
                ("New Balance", "₹{{ new_balance|default:'15,450.00' }}"),
            ],
            card2_icon="&#128179;",
            card2_title="Payment Method",
            card2_items=[
                ("Channel", "{{ payment_method|default:'Razorpay Net Banking' }}"),
                ("Settlement Status", "Instant Settlement Complete"),
            ],
            next_step_desc="Your wallet balance is immediately active for contact unlocks and AI search discovery.",
            cta_label="View Wallet & Transactions",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Wallet recharged with ₹{{ amount }} (Ref: {{ transaction_ref }}). New balance: ₹{{ new_balance }}. Details: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "amount", "transaction_ref", "new_balance", "payment_method", "cta_url"],
        "rule": {
            "name": "Notify Admin on Wallet Recharge Success",
            "description": "Dispatched to company billing contacts upon successful wallet top-up.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # Invoice Generated
    {
        "event_key": "billing.invoice_generated",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Tax Invoice Generated",
        "subject": "Tax Invoice Generated: {{ invoice_number }} for ₹{{ total_amount|default:amount }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Team' }}",
            intro_text="An official GST-compliant tax invoice has been generated for your transaction.",
            card1_icon="&#128196;",
            card1_title="Invoice Breakdown",
            card1_items=[
                ("Invoice Number", "{{ invoice_number|default:'INV-2026-001' }}"),
                ("Invoice Date", "{{ invoice_date|default:'Today' }}"),
                ("Subtotal", "₹{{ subtotal|default:'8,474.58' }}"),
                ("GST (18%)", "₹{{ tax_amount|default:'1,525.42' }}"),
                ("Total Amount", "₹{{ total_amount|default:'10,000.00' }}"),
            ],
            card2_icon="&#127970;",
            card2_title="Billed Entity",
            card2_items=[
                ("Billed To", "{{ company_name|default:'Apex Enterprise' }}"),
                ("GST / Tax ID", "{{ tax_id|default:'27AABCT3521G1Z0' }}"),
            ],
            next_step_desc="Download the official PDF invoice for your accounting and taxation records.",
            cta_label="Download PDF Invoice",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#0d9488",
        ),
        "text_content": "Tax Invoice {{ invoice_number }} generated for ₹{{ total_amount|default:amount }}. Download from: {{ cta_url }}.",
        "variables": ["recipient_name", "company_name", "invoice_number", "invoice_date", "subtotal", "tax_amount", "total_amount", "tax_id", "cta_url"],
        "rule": {
            "name": "Notify Company on Tax Invoice Generation",
            "description": "Sends GST tax invoice copy to billing contacts upon completed transaction.",
            "target_audience": NotificationRule.TargetAudience.COMPANY_ADMIN,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # AI Search Completed
    {
        "event_key": "search.ai_completed",
        "category": NotificationTemplate.Category.SYSTEM,
        "name": "AI Search Completed",
        "subject": "AI Discovery Complete: {{ count|default:'12' }} Matches for {{ query_title }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Sourcing Officer' }}",
            intro_text="Autonomous intelligence search has concluded scanning corporate registries and supplier networks.",
            card1_icon="&#10024;",
            card1_title="Discovery Results",
            card1_items=[
                ("Search Query", "{{ query_title|default:'Industrial Equipment' }}"),
                ("Matched Entities", "{{ count|default:'12' }} Organizations"),
            ],
            card2_icon="&#9889;",
            card2_title="Engine Metrics",
            card2_items=[
                ("Match Accuracy", "{{ confidence_score|default:'98%' }} Confidence"),
                ("Execution Time", "{{ execution_time|default:'1.4s' }}"),
            ],
            next_step_desc="Explore pre-qualified entities, review match justifications, and initiate contact.",
            cta_label="View Search Results",
            cta_url_expr="{{ cta_url|default:'/ai-search/' }}",
            cta_bg="#7c3aed",
        ),
        "text_content": "AI search complete for {{ query_title }}: {{ count }} matches found. View at: {{ cta_url }}.",
        "variables": ["recipient_name", "query_title", "count", "confidence_score", "execution_time", "cta_url"],
        "rule": {
            "name": "Notify User When AI Search Concludes",
            "description": "Alerts acting user when deep AI crawler and embedding matching finishes.",
            "target_audience": NotificationRule.TargetAudience.USER,
            "delivery_mode": NotificationRule.DeliveryMode.AUTOMATIC,
            "module": NotificationRule.ModuleType.AI_SEARCH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },
    # Legacy / Additional: seller.new_buyer_lead
    {
        "event_key": "seller.new_buyer_lead",
        "category": NotificationTemplate.Category.SELLER,
        "name": "New Buyer Lead Discovered",
        "subject": "New Buyer Lead Discovered: {{ requirement_title }} [{{ buyer_company }}]",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Sales Team' }}",
            intro_text="A new high-intent buyer requirement matching your catalog offerings has just been published on TradeConnect. Engage early to secure the order.",
            card1_icon="&#127919;",  # Target
            card1_title="Buyer Lead Profile",
            card1_items=[
                ("Buyer Organization", "{{ buyer_company|default:'Global Infrastructure Corp' }}"),
                ("Industry Sector", "{{ category|default:'Heavy Engineering & Construction' }}"),
                ("Sourcing Requirement", "{{ requirement_title|default:'Heavy Duty Hydraulic Pump Assembly' }}"),
                ("Sourcing Location", "{{ sourcing_location|default:'Pune, Maharashtra' }}"),
            ],
            card2_icon="&#128202;",  # Bar chart
            card2_title="Procurement Scope",
            card2_items=[
                ("Target Quantity", "{{ quantity|default:'250 Units' }}"),
                ("Estimated Budget", "{{ budget_range|default:'₹15,00,000 - ₹20,00,000' }}"),
                ("Lead Time / Urgency", "{{ lead_time|default:'Immediate (Within 2 Weeks)' }}"),
                ("Match Confidence", "{{ match_score|default:'96% Compatibility Score' }}"),
            ],
            next_step_desc="Review the buyer's technical parameters and submit your direct commercial quotation before competitor vendors respond.",
            cta_label="Pitch & Submit Quote",
            cta_url_expr="{{ cta_url|default:'/sales/buyer-leads/' }}",
            secondary_label="View All Buyer Leads",
            secondary_url_expr="{{ dashboard_url|default:'/sales/find-buyers/' }}",
            cta_bg="#4f46e5",
        ),
        "text_content": "New Buyer Lead Discovered: {{ requirement_title }} from {{ buyer_company }}. Submit quote at: {{ cta_url }}",
        "variables": [
            "recipient_name", "buyer_company", "category", "requirement_title",
            "sourcing_location", "quantity", "budget_range", "lead_time",
            "match_score", "cta_url", "dashboard_url",
        ],
        "rule": {
            "name": "Notify Vendor on New Matching Buyer Lead",
            "module": NotificationRule.ModuleType.SALES,
            "company_type": NotificationRule.CompanyTypeScope.SELLER,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Legacy / Additional: admin.critical_system_alert
    {
        "event_key": "admin.critical_system_alert",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Critical System Alert",
        "subject": "CRITICAL ALERT: {{ incident_title }} [{{ service_name }}]",
        "html_content": build_card_body_html(
            greeting_name_expr="Super Admin Operations",
            intro_text="A critical system anomaly has occurred that requires immediate administrative review and remediation to ensure uninterrupted operations.",
            card1_icon="&#9888;",  # Warning
            card1_title="Incident Specifications",
            card1_items=[
                ("Incident Identifier", "{{ incident_code|default:'INC-AI-9021' }}"),
                ("Affected Service", "{{ service_name|default:'Pinecone / FAISS Embedding Pipeline' }}"),
                ("Trigger Timestamp", "{{ timestamp|default:'2026-10-13 11:32:04 UTC' }}"),
                ("Severity Rating", "{{ severity_level|default:'CRITICAL (P1)' }}"),
            ],
            card2_icon="&#128269;",  # Diagnostics
            card2_title="Error Diagnostics",
            card2_items=[
                ("Diagnostic Summary", "{{ error_summary|default:'Upstream timeout exceeded 5000ms threshold.' }}"),
                ("Impact Scope", "{{ impacted_operations|default:'Autonomous discovery query fallback active' }}"),
                ("Failover Status", "{{ retry_status|default:'Automated circuit breaker engaged' }}"),
                ("Host Environment", "{{ environment|default:'Production Node cluster-01' }}"),
            ],
            next_step_desc="Immediate Super Admin intervention required. Open the executive console to inspect logs and dispatch recovery.",
            cta_label="Open Super Admin Console",
            cta_url_expr="{{ cta_url|default:'/admin-panel/' }}",
            secondary_label="Inspect System Logs",
            secondary_url_expr="{{ logs_url|default:'/admin-panel/audit-logs/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "CRITICAL ALERT: {{ incident_title }} in {{ service_name }}. Code: {{ incident_code }}. Open Admin Console: {{ cta_url }}",
        "variables": [
            "incident_title", "incident_code", "service_name", "timestamp",
            "severity_level", "error_summary", "impacted_operations",
            "retry_status", "environment", "cta_url", "logs_url",
        ],
        "is_system": True,
        "rule": {
            "name": "Alert Super Admins on Critical Failure",
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # Legacy / Additional: billing.low_wallet_balance
    {
        "event_key": "billing.low_wallet_balance",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Low Wallet Balance Warning",
        "subject": "Action Required: Low Wallet Balance Alert [₹{{ current_balance }} Remaining]",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Admin' }}",
            intro_text="Your company wallet balance has fallen below the recommended operational threshold. To prevent interruption of automated AI searches and lead unlocks, please top up your balance.",
            card1_icon="&#9888;",  # Warning
            card1_title="Wallet Status",
            card1_items=[
                ("Available Balance", "₹{{ current_balance|default:'450.00' }}"),
                ("Safety Threshold", "₹{{ threshold_amount|default:'2,000.00' }}"),
                ("Enterprise Account", "{{ company_name|default:'Sample Enterprise Ltd' }}"),
                ("Last Debited Action", "{{ last_action|default:'AI Supplier Contact Unlock (-₹50.00)' }}"),
            ],
            card2_icon="&#128201;",  # Decreasing chart
            card2_title="Impact Assessment",
            card2_items=[
                ("Pipelines at Risk", "{{ searches_at_risk|default:'Direct RFQs & Contact Unlocks' }}"),
                ("Discovery Throttling", "{{ unlocks_status|default:'Pending wallet top-up' }}"),
                ("Estimated Runway", "{{ days_remaining|default:'Under 2 days of typical operations' }}"),
                ("Auto-Pause Protection", "Active when zero balance reached"),
            ],
            next_step_desc="Recharge your company wallet immediately to ensure uninterrupted supplier matching and lead capture.",
            cta_label="Recharge Wallet Now",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            secondary_label="View Usage Breakdown",
            secondary_url_expr="{{ usage_url|default:'/company/billing/' }}",
            cta_bg="#d97706",
        ),
        "text_content": "Low Wallet Balance Alert: ₹{{ current_balance }} remaining. Threshold: ₹{{ threshold_amount }}. Recharge now at: {{ cta_url }}",
        "variables": [
            "recipient_name", "current_balance", "threshold_amount", "company_name",
            "last_action", "searches_at_risk", "unlocks_status", "days_remaining",
            "cta_url", "usage_url",
        ],
        "rule": {
            "name": "Low Wallet Balance Alert",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Legacy / Additional: billing.subscription_expiring
    {
        "event_key": "billing.subscription_expiring",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Subscription Expiring Reminder",
        "subject": "Reminder: Your {{ plan_name }} Subscription Expires in {{ days_remaining }} Days",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Account Admin' }}",
            intro_text="Your enterprise subscription plan is scheduled to expire in {{ days_remaining|default:'5' }} days. Renew now to retain uninterrupted premium features and AI search allocations.",
            card1_icon="&#128197;",  # Calendar
            card1_title="Current Plan Details",
            card1_items=[
                ("Active Plan Tier", "{{ plan_name|default:'Enterprise Growth Tier' }}"),
                ("Scheduled Expiration", "{{ expiry_date|default:'18 Oct 2026' }}"),
                ("Days Remaining", "{{ days_remaining|default:'5' }} Days"),
                ("Billing Cycle", "{{ billing_cycle|default:'Annual Enterprise Subscription' }}"),
            ],
            card2_icon="&#11088;",  # Star
            card2_title="Plan Entitlements at Risk",
            card2_items=[
                ("Active Negotiations", "{{ active_inquiries|default:'24 Deal Inquiries' }}"),
                ("Monthly AI Allowance", "{{ search_quota|default:'500 AI Searches / Month' }}"),
                ("Vendor Trust Badge", "{{ badge_status|default:'Verified Gold Supplier Badge' }}"),
                ("Support Priority", "{{ support_tier|default:'24/7 Dedicated Account Manager' }}"),
            ],
            next_step_desc="Renew now or upgrade your plan to maintain unbroken business operations and unlock corporate benefits.",
            cta_label="Renew Subscription Now",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            secondary_label="Compare Plans",
            secondary_url_expr="{{ plans_url|default:'/company/billing/plans/' }}",
            cta_bg="#d97706",
        ),
        "text_content": "Your subscription for {{ plan_name }} expires in {{ days_remaining }} days. Renew today at: {{ cta_url }}",
        "variables": [
            "recipient_name", "plan_name", "expiry_date", "days_remaining",
            "billing_cycle", "active_inquiries", "search_quota", "badge_status",
            "support_tier", "cta_url", "plans_url",
        ],
        "rule": {
            "name": "Subscription Renewal Warning",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Legacy / Additional: admin.company_registered
    {
        "event_key": "admin.company_registered",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "New Company Registered Alert",
        "subject": "Platform Notice: New Company Registered - {{ company_name }} ({{ company_type }})",
        "html_content": build_card_body_html(
            greeting_name_expr="Super Admin Operations",
            intro_text="A new enterprise organization has completed registration on the TradeConnect platform and is awaiting administrative verification.",
            card1_icon="&#127970;",  # Building
            card1_title="Company Profile",
            card1_items=[
                ("Organization Name", "{{ company_name|default:'Sample Enterprise Ltd' }}"),
                ("Registered Account Type", "{{ company_type|default:'Vendor & Sales (Both)' }}"),
                ("Industry Sector", "{{ industry|default:'Precision Engineering & Manufacturing' }}"),
                ("Registration Date", "{{ registered_at|default:'13 Oct 2026, 11:15 AM' }}"),
            ],
            card2_icon="&#128100;",  # User
            card2_title="Primary Administrator",
            card2_items=[
                ("Lead Signatory", "{{ admin_name|default:'Vikramaditya Roy' }}"),
                ("Corporate Email", "{{ contact_email|default:'vikram@sampleenterprise.com' }}"),
                ("Contact Number", "{{ admin_phone|default:'+91 98200 11223' }}"),
                ("Headquarters", "{{ location|default:'Mumbai, Maharashtra, India' }}"),
            ],
            next_step_desc="Review company documentation, verify GSTIN/CIN records, and grant platform operational permissions.",
            cta_label="Review & Verify Company",
            cta_url_expr="{{ cta_url|default:'/admin-panel/companies/' }}",
            secondary_label="View All Companies",
            secondary_url_expr="{{ companies_url|default:'/admin-panel/companies/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "New Company Registered: {{ company_name }} ({{ company_type }}). Contact: {{ contact_email }}. Review at: {{ cta_url }}",
        "variables": [
            "company_name", "company_type", "industry", "registered_at",
            "admin_name", "contact_email", "admin_phone", "location",
            "cta_url", "companies_url",
        ],
        "rule": {
            "name": "Alert Super Admins on New Company Registration",
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # Legacy / Additional: reports.export_ready
    {
        "event_key": "reports.export_ready",
        "category": NotificationTemplate.Category.BOTH,
        "name": "Export Report Ready",
        "subject": "Your Export Report is Ready: {{ report_name }} [{{ record_count }} Records]",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Enterprise User' }}",
            intro_text="Your requested data export has been compiled and is ready for secure download. The download link will remain active for 48 hours.",
            card1_icon="&#128229;",  # Inbox / download
            card1_title="Report Details",
            card1_items=[
                ("Report Title", "{{ report_name|default:'Q3 Prequalified Supplier Intelligence Dossier' }}"),
                ("Document Format", "{{ report_format|default:'Microsoft Excel (.xlsx)' }}"),
                ("Export Volume", "{{ record_count|default:'142 Records' }}"),
                ("Dossier File Size", "{{ file_size|default:'2.8 MB' }}"),
            ],
            card2_icon="&#128202;",  # Bar chart
            card2_title="Export Scope",
            card2_items=[
                ("Generated By", "{{ requested_by|default:'Procurement Team Admin' }}"),
                ("Compilation Date", "{{ generated_at|default:'13 Oct 2026, 11:40 AM' }}"),
                ("Filters Configured", "{{ filters_applied|default:'Western India, MSME & ISO 9001' }}"),
                ("Secure Link Expiry", "{{ expires_at|default:'48 Hours from Generation' }}"),
            ],
            next_step_desc="Click below to download your complete encrypted intelligence report directly to your device.",
            cta_label="Download Report Now",
            cta_url_expr="{{ cta_url|default:'#' }}",
            secondary_label="Go to Reports Desk",
            secondary_url_expr="{{ reports_url|default:'/company/reports/' }}",
            cta_bg="#0d9488",
        ),
        "text_content": "Your export report '{{ report_name }}' ({{ record_count }} records) is ready for download: {{ cta_url }}",
        "variables": [
            "recipient_name", "report_name", "report_format", "record_count",
            "file_size", "requested_by", "generated_at", "filters_applied",
            "expires_at", "cta_url", "reports_url",
        ],
        "rule": {
            "name": "Notify User When Export Dossier is Ready",
            "module": NotificationRule.ModuleType.PLATFORM,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # Legacy / Additional: billing.wallet_recharged
    {
        "event_key": "billing.wallet_recharged",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Wallet Recharge Successful (Legacy Key)",
        "subject": "Wallet Recharged: +₹{{ amount }} Credited Successfully",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Admin' }}",
            intro_text="Your company wallet has been credited with ₹{{ amount }}. Available balance is ₹{{ new_balance }}.",
            card1_icon="&#128179;",
            card1_title="Transaction Summary",
            card1_items=[
                ("Credited Amount", "₹{{ amount }}"),
                ("Transaction Ref", "{{ transaction_ref }}"),
                ("New Balance", "₹{{ new_balance }}"),
                ("Status", "Completed"),
            ],
            card2_icon="&#128176;",
            card2_title="Account Balance",
            card2_items=[
                ("Available Balance", "₹{{ new_balance }}"),
                ("Wallet State", "Active"),
            ],
            next_step_desc="Continue discovering verified suppliers and capturing qualified leads.",
            cta_label="View Wallet",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Wallet credited with ₹{{ amount }}. New balance: ₹{{ new_balance }}.",
        "variables": ["recipient_name", "amount", "transaction_ref", "new_balance", "cta_url"],
        "rule": {
            "name": "Notify on Wallet Top-Up (Legacy)",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Legacy / Additional: billing.low_balance
    {
        "event_key": "billing.low_balance",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Low Wallet Balance Warning (Legacy Key)",
        "subject": "Action Required: Low Wallet Balance Alert (₹{{ current_balance }} remaining)",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Admin' }}",
            intro_text="Your company wallet balance has fallen below the operational threshold. Current balance: ₹{{ current_balance }}.",
            card1_icon="&#9888;",
            card1_title="Balance Status",
            card1_items=[
                ("Current Balance", "₹{{ current_balance }}"),
                ("Threshold", "Recommended Top-up Required"),
            ],
            card2_icon="&#128201;",
            card2_title="Operational Impact",
            card2_items=[
                ("Pending Searches", "At Risk"),
                ("Status", "Recharge Promptly"),
            ],
            next_step_desc="Recharge your wallet to prevent interruption of AI searches.",
            cta_label="Recharge Wallet",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            cta_bg="#d97706",
        ),
        "text_content": "Low wallet balance: ₹{{ current_balance }} remaining. Recharge at {{ cta_url }}",
        "variables": ["recipient_name", "current_balance", "cta_url"],
        "rule": {
            "name": "Low Balance Warning (Legacy)",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.WARNING,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # Legacy / Additional: buyer.search_completed
    {
        "event_key": "buyer.search_completed",
        "category": NotificationTemplate.Category.BUYER,
        "name": "Supplier Discovery Search Completed (Legacy Key)",
        "subject": "Supplier Search Completed: {{ count }} Matching Suppliers Discovered",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Procurement Specialist' }}",
            intro_text="Your AI supplier discovery for {{ query_title }} has finished. Identified {{ count }} qualified vendors.",
            card1_icon="&#128269;",
            card1_title="Search Overview",
            card1_items=[
                ("Search Query", "{{ query_title }}"),
                ("Suppliers Discovered", "{{ count }} Vendors"),
            ],
            card2_icon="&#10024;",
            card2_title="Results Summary",
            card2_items=[
                ("Match Status", "Completed"),
                ("Next Action", "Review Profiles"),
            ],
            next_step_desc="Log in to review vendor profiles, compare quotes, and dispatch direct RFQs.",
            cta_label="View Suppliers",
            cta_url_expr="{{ cta_url|default:'/procurement/find-suppliers/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Supplier search completed: {{ count }} suppliers found for {{ query_title }}.",
        "variables": ["recipient_name", "query_title", "count", "cta_url"],
        "rule": {
            "name": "Notify Buyer When Supplier Search Completes (Legacy)",
            "module": NotificationRule.ModuleType.AI_SEARCH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # Legacy / Additional: seller.search_completed
    {
        "event_key": "seller.search_completed",
        "category": NotificationTemplate.Category.SELLER,
        "name": "Buyer Lead Discovery Completed (Legacy Key)",
        "subject": "Buyer Leads Discovery: {{ count }} Prospective Buyers Found",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Sales Team' }}",
            intro_text="Your search for potential purchasing buyers for {{ query_title }} is complete. Identified {{ count }} organizations.",
            card1_icon="&#127919;",
            card1_title="Search Overview",
            card1_items=[
                ("Catalog Item", "{{ query_title }}"),
                ("Prospective Buyers", "{{ count }} Organizations"),
            ],
            card2_icon="&#128202;",
            card2_title="Lead Opportunities",
            card2_items=[
                ("Status", "Active"),
                ("Next Action", "Engage Buyers"),
            ],
            next_step_desc="Review high-intent purchasing leads and dispatch commercial proposals.",
            cta_label="View Buyer Leads",
            cta_url_expr="{{ cta_url|default:'/sales/find-buyers/' }}",
            cta_bg="#4f46e5",
        ),
        "text_content": "Found {{ count }} prospective buyer leads for {{ query_title }}.",
        "variables": ["recipient_name", "query_title", "count", "cta_url"],
        "rule": {
            "name": "Notify Vendor When Buyer Search Completes (Legacy)",
            "module": NotificationRule.ModuleType.AI_SEARCH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # Legacy / Additional: admin.ai_provider_error
    {
        "event_key": "admin.ai_provider_error",
        "category": NotificationTemplate.Category.ADMIN,
        "name": "Super Admin Alert: AI Provider Failure",
        "subject": "CRITICAL: AI Provider Error in {{ service_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="Super Admin Critical Desk",
            intro_text="An autonomous AI search or embedding provider failure occurred in {{ service_name }}.",
            card1_icon="&#9888;",
            card1_title="Failure Details",
            card1_items=[
                ("Service Name", "{{ service_name }}"),
                ("Error Message", "{{ error_message }}"),
            ],
            card2_icon="&#128269;",
            card2_title="System Impact",
            card2_items=[
                ("Fallback Pipeline", "Active"),
                ("Action Needed", "Inspect API credentials"),
            ],
            next_step_desc="Inspect upstream provider status and switch to secondary model provider if needed.",
            cta_label="Inspect AI Providers",
            cta_url_expr="{{ cta_url|default:'/admin-panel/' }}",
            cta_bg="#dc2626",
        ),
        "text_content": "AI Provider error in {{ service_name }}: {{ error_message }}.",
        "variables": ["service_name", "error_message", "cta_url"],
        "rule": {
            "name": "Alert Super Admins on AI Provider Failure",
            "module": NotificationRule.ModuleType.ADMIN,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.SUPER_ADMINS,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

]


class Command(BaseCommand):
    help = "Seeds all 21 standard notification templates and event rules across User, Company Admin, and Super Admin categories."

    def handle(self, *args, **options):
        self.stdout.write("Seeding all 21 standardized email notification templates & trigger rules...")
        created_tpl_count = 0
        updated_tpl_count = 0
        created_rule_count = 0
        updated_rule_count = 0

        with transaction.atomic():
            for item in DEFAULT_TEMPLATES:
                event_key = item["event_key"]
                tpl_data = {
                    "name": item["name"],
                    "category": item["category"],
                    "subject": item["subject"],
                    "html_content": item["html_content"].strip(),
                    "text_content": item["text_content"].strip(),
                    "variables": item["variables"],
                    "is_active": True,
                    "is_system": item.get("is_system", False),
                }

                template, created = NotificationTemplate.objects.update_or_create(
                    event_key=event_key,
                    defaults=tpl_data,
                )
                if created:
                    created_tpl_count += 1
                else:
                    updated_tpl_count += 1

                rule_data = item.get("rule")
                if rule_data:
                    rule_defaults = {
                        "name": rule_data["name"],
                        "description": rule_data.get("description", ""),
                        "target_audience": rule_data.get("target_audience", NotificationRule.TargetAudience.USER),
                        "delivery_mode": rule_data.get("delivery_mode", NotificationRule.DeliveryMode.AUTOMATIC),
                        "module": rule_data.get("module", NotificationRule.ModuleType.PLATFORM),
                        "company_type": rule_data.get("company_type", NotificationRule.CompanyTypeScope.ALL),
                        "template": template,
                        "recipient_type": rule_data["recipient_type"],
                        "severity": rule_data.get("severity", NotificationRule.Severity.INFO),
                        "priority": rule_data.get("priority", NotificationRule.Priority.NORMAL),
                        "is_mandatory_auth": rule_data.get("is_mandatory_auth", False),
                        "allow_manual_send": rule_data.get("allow_manual_send", True),
                        "threshold_value": rule_data.get("threshold_value"),
                        "cooldown_hours": rule_data.get("cooldown_hours", 24),
                        "reminder_days_before": rule_data.get("reminder_days_before", 5),
                        "is_enabled": True,
                    }

                    existing_rule = NotificationRule.objects.filter(event_key=event_key).first()
                    if existing_rule:
                        for k, v in rule_defaults.items():
                            setattr(existing_rule, k, v)
                        existing_rule.save()
                        # Clean up duplicate rules with the same event_key
                        NotificationRule.objects.filter(event_key=event_key).exclude(id=existing_rule.id).delete()
                        updated_rule_count += 1
                    else:
                        NotificationRule.objects.create(
                            event_key=event_key,
                            **rule_defaults,
                        )
                        created_rule_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded: {created_tpl_count} templates created, {updated_tpl_count} updated. "
                f"{created_rule_count} rules created, {updated_rule_count} updated."
            )
        )
