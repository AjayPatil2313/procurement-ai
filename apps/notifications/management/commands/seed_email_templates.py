from django.core.management.base import BaseCommand
from django.db import transaction
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
    the TradeConnect B2B reference design (media_1791871744877_b957beac.jpg).
    This snippet occupies the dynamic body slot of the master layout.
    """
    # Card 1 2-column table rows
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

    # Card 2 2-column table rows
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

    # Secondary button html
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
    # 1. New Inquiry Received (Vendor / Seller)
    # =========================================================================
    {
        "event_key": "seller.inquiry_received",
        "category": NotificationTemplate.Category.SELLER,
        "name": "New Inquiry Received",
        "subject": "New Inquiry Received: {{ buyer_company }} for {{ product_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Seller Team' }}",
            intro_text="A verified buyer has shown direct interest in your product and submitted an inquiry. Kindly review the details below and respond at your earliest convenience.",
            card1_icon="&#128100;",  # User
            card1_title="Buyer Details",
            card1_items=[
                ("Company Name", "{{ buyer_company|default:'ABC Industries Pvt. Ltd.' }}"),
                ("Phone", "{{ buyer_phone|default:'+91 98765 43210' }}"),
                ("Contact Person", "{{ contact_person|default:'Rahul Sharma' }}"),
                ("Location", "{{ buyer_location|default:'Ahmedabad, Gujarat' }}"),
                ("Email", "{{ buyer_email|default:'rahul@abcindustries.com' }}"),
                ("Company Type", "{{ company_type|default:'Private Limited (Pvt Ltd)' }}"),
            ],
            card2_icon="&#128230;",  # Package
            card2_title="Product Requirement",
            card2_items=[
                ("Product Name", "{{ product_name|default:'PP Woven Bag' }}"),
                ("Quantity Required", "{{ quantity|default:'10,000 Bags' }}"),
                ("Specification", "{{ specification|default:'5 kg Capacity, Food Grade' }}"),
                ("Expected Price Range", "{{ price_range|default:'₹ 18 - ₹ 22 per bag' }}"),
            ],
            next_step_desc="You can view the full inquiry details, submit an official price quotation, or get in touch directly from your dashboard.",
            cta_label="View Inquiry",
            cta_url_expr="{{ cta_url|default:'/sales/inquiries/' }}",
            secondary_label="Go to Vendor Dashboard",
            secondary_url_expr="{{ dashboard_url|default:'/dashboard/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "New Inquiry Received from {{ buyer_company }} for {{ product_name }} (Qty: {{ quantity }}). Review details and submit your quotation at: {{ cta_url }}",
        "variables": [
            "recipient_name", "buyer_company", "contact_person", "buyer_email",
            "buyer_phone", "buyer_location", "company_type", "product_name",
            "quantity", "specification", "price_range", "cta_url", "dashboard_url",
        ],
        "rule": {
            "name": "Notify Vendor on New Inbound Inquiry",
            "module": NotificationRule.ModuleType.SALES,
            "company_type": NotificationRule.CompanyTypeScope.SELLER,
            "recipient_type": NotificationRule.RecipientType.COMPANY_ADMINS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # =========================================================================
    # 2. Matching Suppliers Found (Buyer / Procurement)
    # =========================================================================
    {
        "event_key": "buyer.matching_suppliers_found",
        "category": NotificationTemplate.Category.BUYER,
        "name": "Matching Suppliers Found",
        "subject": "Matching Suppliers Found: {{ count }} Verified Vendors for {{ requirement_title }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Procurement Team' }}",
            intro_text="Our autonomous AI matching engine has completed scanning supplier catalogs and identified {{ count|default:'14' }} verified vendors matching your sourcing specifications.",
            card1_icon="&#128203;",  # Clipboard
            card1_title="Sourcing Requirement",
            card1_items=[
                ("Requirement Title", "{{ requirement_title|default:'Industrial Stainless Steel Valves (Grade 316)' }}"),
                ("Category", "{{ category|default:'Industrial Machinery & Valves' }}"),
                ("Quantity Required", "{{ quantity|default:'500 Units' }}"),
                ("Target Delivery", "{{ delivery_timeline|default:'Within 15 Days' }}"),
            ],
            card2_icon="&#128269;",  # Search
            card2_title="Supplier Matches Overview",
            card2_items=[
                ("Matches Discovered", "{{ count|default:'14' }} Verified Suppliers"),
                ("Top Matched Supplier", "{{ top_supplier|default:'Apex Engineering Works' }}"),
                ("Verification Status", "{{ verification_status|default:'GST & MSME Verified' }}"),
                ("Best Estimated Price", "{{ estimated_price|default:'₹15,000 / unit' }}"),
            ],
            next_step_desc="Compare supplier profiles, inspect compliance accreditation, and dispatch direct RFQs to all matching vendors with a single click.",
            cta_label="Review Suppliers & Send RFQ",
            cta_url_expr="{{ cta_url|default:'/procurement/find-suppliers/' }}",
            secondary_label="Go to Procurement Dashboard",
            secondary_url_expr="{{ dashboard_url|default:'/dashboard/' }}",
            cta_bg="#0284c7",
        ),
        "text_content": "Matching Suppliers Found: {{ count }} verified vendors discovered for {{ requirement_title }}. Review suppliers and dispatch RFQs at: {{ cta_url }}",
        "variables": [
            "recipient_name", "requirement_title", "category", "quantity",
            "delivery_timeline", "count", "top_supplier", "verification_status",
            "estimated_price", "cta_url", "dashboard_url",
        ],
        "rule": {
            "name": "Notify Buyer When Supplier Matches Found",
            "module": NotificationRule.ModuleType.AI_SEARCH,
            "company_type": NotificationRule.CompanyTypeScope.BUYER,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # =========================================================================
    # 3. New Buyer Lead (Vendor / Seller)
    # =========================================================================
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

    # =========================================================================
    # 4. Wallet Recharge Successful (Billing / All)
    # =========================================================================
    {
        "event_key": "billing.wallet_recharge_success",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Wallet Recharge Successful",
        "subject": "Wallet Recharge Successful: +₹{{ amount }} Credited [Ref: {{ transaction_ref }}]",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Admin' }}",
            intro_text="Your company wallet has been successfully recharged with ₹{{ amount }}. The funds are immediately available for AI search queries, contact unlocks, and platform operations.",
            card1_icon="&#128179;",  # Credit card
            card1_title="Transaction Summary",
            card1_items=[
                ("Credited Amount", "+₹{{ amount|default:'25,000.00' }}"),
                ("Transaction Reference", "{{ transaction_ref|default:'TXN-DEMO-88210' }}"),
                ("Payment Method", "{{ payment_method|default:'Razorpay Corporate Net Banking' }}"),
                ("Settlement Timestamp", "{{ transaction_time|default:'Instant Digital Settlement' }}"),
            ],
            card2_icon="&#128176;",  # Money bag
            card2_title="Account Balance Metrics",
            card2_items=[
                ("Previous Balance", "₹{{ previous_balance|default:'7,450.00' }}"),
                ("Top-Up Credited", "+₹{{ amount|default:'25,000.00' }}"),
                ("New Available Balance", "₹{{ new_balance|default:'32,450.00' }}"),
                ("Wallet Operational State", "{{ wallet_status|default:'Active & Verified' }}"),
            ],
            next_step_desc="Your credits are live. You can initiate deep AI partner searches, unlock contact numbers, and export dossiers without interruption.",
            cta_label="View Wallet & Transactions",
            cta_url_expr="{{ cta_url|default:'/company/billing/' }}",
            secondary_label="Download Payment Receipt",
            secondary_url_expr="{{ receipt_url|default:'/company/billing/receipts/' }}",
            cta_bg="#059669",
        ),
        "text_content": "Wallet Recharge Successful: +₹{{ amount }} credited. New Balance: ₹{{ new_balance }}. Ref: {{ transaction_ref }}. View transactions at: {{ cta_url }}",
        "variables": [
            "recipient_name", "amount", "transaction_ref", "payment_method",
            "transaction_time", "previous_balance", "new_balance", "wallet_status",
            "cta_url", "receipt_url",
        ],
        "rule": {
            "name": "Notify on Successful Wallet Recharge",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.HIGH,
        },
    },

    # =========================================================================
    # 5. Invoice Generated (Billing / All)
    # =========================================================================
    {
        "event_key": "billing.invoice_generated",
        "category": NotificationTemplate.Category.BILLING,
        "name": "Tax Invoice Generated",
        "subject": "Tax Invoice Generated: {{ invoice_number }} [₹{{ amount }}]",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Finance Team' }}",
            intro_text="An official tax invoice has been generated for your recent billing transaction or subscription renewal on the platform. The document is archived for your accounting records.",
            card1_icon="&#128196;",  # Document
            card1_title="Invoice Overview",
            card1_items=[
                ("Invoice Number", "{{ invoice_number|default:'INV-2026-0042' }}"),
                ("Invoice Date", "{{ invoice_date|default:'13 Oct 2026' }}"),
                ("Payment Terms", "{{ due_date|default:'Paid in Full (Instant)' }}"),
                ("Supplier GSTIN", "{{ tax_id|default:'27AABCT3521G1Z0' }}"),
            ],
            card2_icon="&#129534;",  # Receipt
            card2_title="Billing Breakdown",
            card2_items=[
                ("Service / Plan", "{{ plan_description|default:'Enterprise Pro Annual Plan (10,000 Credits)' }}"),
                ("Net Subtotal", "₹{{ subtotal|default:'21,186.44' }}"),
                ("GST Tax Amount (18%)", "₹{{ tax_amount|default:'3,813.56' }}"),
                ("Total Paid Amount", "₹{{ amount|default:'25,000.00' }}"),
            ],
            next_step_desc="Your official GST tax invoice has been generated and archived. You can download the PDF copy for accounting and GST input claims.",
            cta_label="Download PDF Invoice",
            cta_url_expr="{{ cta_url|default:'/company/billing/invoices/' }}",
            secondary_label="View All Invoices",
            secondary_url_expr="{{ invoices_url|default:'/company/billing/' }}",
            cta_bg="#0d9488",
        ),
        "text_content": "Tax Invoice {{ invoice_number }} generated for ₹{{ amount }}. Download your PDF invoice from: {{ cta_url }}",
        "variables": [
            "recipient_name", "invoice_number", "invoice_date", "due_date",
            "tax_id", "plan_description", "subtotal", "tax_amount",
            "amount", "cta_url", "invoices_url",
        ],
        "rule": {
            "name": "Notify on Tax Invoice Generation",
            "module": NotificationRule.ModuleType.BILLING,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.BILLING_CONTACTS,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # =========================================================================
    # 6. AI Search Completed (AI Search / All)
    # =========================================================================
    {
        "event_key": "search.ai_completed",
        "category": NotificationTemplate.Category.BUYER,
        "name": "AI Search Completed",
        "subject": "AI Search Completed: {{ count }} Results Discovered for \"{{ query_title }}\"",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ recipient_name|default:'Procurement Specialist' }}",
            intro_text="Your autonomous AI intelligence search for enterprise partners has completed scanning our global verified B2B database.",
            card1_icon="&#129302;",  # Robot
            card1_title="Search Parameters",
            card1_items=[
                ("Search Query", "{{ query_title|default:'Precision CNC Machining Suppliers' }}"),
                ("Filter Parameters", "{{ category|default:'Western India, MSME & ISO Certified' }}"),
                ("Execution Latency", "{{ execution_time|default:'1.42 Seconds' }}"),
                ("Database Scope", "{{ search_scope|default:'Global Verified B2B Network' }}"),
            ],
            card2_icon="&#10024;",  # Sparkles
            card2_title="Intelligence Results",
            card2_items=[
                ("Suppliers Scanned", "{{ scanned_count|default:'1,240 Organizations' }}"),
                ("Matching Partners Found", "{{ count|default:'14' }} Verified Vendors"),
                ("Pre-Qualified Entities", "{{ prequalified_count|default:'10 Certified Suppliers' }}"),
                ("AI Confidence Score", "{{ confidence_score|default:'98.4% Match Precision' }}"),
            ],
            next_step_desc="Review partner dossiers, evaluate manufacturing ratings, and trigger direct quotation requests.",
            cta_label="Explore Search Results",
            cta_url_expr="{{ cta_url|default:'/procurement/find-suppliers/' }}",
            secondary_label="Export Results CSV",
            secondary_url_expr="{{ export_url|default:'/company/reports/' }}",
            cta_bg="#4f46e5",
        ),
        "text_content": "AI Search Completed: {{ count }} results discovered for {{ query_title }}. Explore results at: {{ cta_url }}",
        "variables": [
            "recipient_name", "query_title", "category", "search_scope",
            "execution_time", "scanned_count", "count", "prequalified_count",
            "confidence_score", "cta_url", "export_url",
        ],
        "rule": {
            "name": "Notify When AI Search Completes",
            "module": NotificationRule.ModuleType.AI_SEARCH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.INFO,
            "priority": NotificationRule.Priority.NORMAL,
        },
    },

    # =========================================================================
    # 7. Password Reset Instructions (Auth / Security)
    # =========================================================================
    {
        "event_key": "auth.password_reset",
        "category": NotificationTemplate.Category.AUTH,
        "name": "Password Reset Instructions",
        "subject": "Password Reset Request for {{ platform_name }}",
        "html_content": build_card_body_html(
            greeting_name_expr="{{ user_name|default:'Enterprise User' }}",
            intro_text="We received a request to reset your password for your TradeConnect enterprise account. If you initiated this request, click the secure button below to choose a new password.",
            card1_icon="&#128274;",  # Lock
            card1_title="Security Request Details",
            card1_items=[
                ("Account Email", "{{ recipient_email|default:'partner@enterprise.com' }}"),
                ("Timestamp", "{{ request_time|default:'13 Oct 2026, 11:30 AM' }}"),
                ("Request IP / Location", "{{ request_ip|default:'103.21.144.10 (Mumbai, IN)' }}"),
                ("Security Validity", "{{ token_expiry|default:'Expires in 24 Hours' }}"),
            ],
            card2_icon="&#128737;",  # Shield
            card2_title="Security Notice",
            card2_items=[
                ("Token Authentication", "Single-use cryptographic reset token."),
                ("Action Required", "If you did not request this, your account is secure and no change will occur."),
            ],
            next_step_desc="Click the secure button below to safely change your password on TradeConnect's encrypted portal.",
            cta_label="Reset My Password",
            cta_url_expr="{{ reset_url|default:'#' }}",
            secondary_label="Contact Security Support",
            secondary_url_expr="{{ support_url|default:'/help-support/' }}",
            cta_bg="#2563eb",
        ),
        "text_content": "Reset your password by clicking this secure link: {{ reset_url }}. If you did not make this request, please contact support.",
        "variables": [
            "user_name", "recipient_email", "request_time", "request_ip",
            "token_expiry", "reset_url", "support_url", "platform_name",
        ],
        "is_system": True,
        "rule": {
            "name": "Password Reset Dispatch",
            "module": NotificationRule.ModuleType.AUTH,
            "company_type": NotificationRule.CompanyTypeScope.ALL,
            "recipient_type": NotificationRule.RecipientType.ACTING_USER,
            "severity": NotificationRule.Severity.CRITICAL,
            "priority": NotificationRule.Priority.URGENT,
        },
    },

    # =========================================================================
    # 8. Critical System Alert (Super Admin Platform Alert)
    # =========================================================================
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

    # =========================================================================
    # 9. Low Wallet Balance Warning (Billing / All)
    # =========================================================================
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

    # =========================================================================
    # 10. Subscription Expiring Soon (Billing / All)
    # =========================================================================
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

    # =========================================================================
    # 11. New Company Registration (Super Admin Alert)
    # =========================================================================
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

    # =========================================================================
    # 12. Export Report Ready (Both / All)
    # =========================================================================
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

    # =========================================================================
    # Additional Workflow & Backward-Compatibility Templates
    # =========================================================================
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
    help = "Seeds standard notification templates and event rules for Buyer, Seller, Both, and Super Admin workflows."

    def handle(self, *args, **options):
        self.stdout.write("Seeding default email notification templates & trigger rules...")
        created_count = 0
        updated_count = 0

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

                rule_data = item.get("rule")
                if rule_data:
                    NotificationRule.objects.update_or_create(
                        event_key=event_key,
                        name=rule_data["name"],
                        defaults={
                            "module": rule_data["module"],
                            "company_type": rule_data["company_type"],
                            "template": template,
                            "recipient_type": rule_data["recipient_type"],
                            "severity": rule_data["severity"],
                            "priority": rule_data["priority"],
                            "is_enabled": True,
                        },
                    )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded notification templates: {created_count} created, {updated_count} updated."
            )
        )
