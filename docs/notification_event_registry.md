# Complete 48-Event Notification Registry & Schema Catalog
**AI Procurement & Sales B2B Platform**
*Authoritative Event Definitions for In-App System and Firebase Cloud Messaging (FCM) Web Push*

---

## 1. Overview & Classification

The platform event catalog defines **48 distinct business events** partitioned into three operational tiers:
1. **Tier 1: User / Company User Events (1 to 20)** — Operational notifications for individual procurement officers, sales representatives, and team members.
2. **Tier 2: Company Admin Events (21 to 34)** — Organizational notifications for workspace administrators covering team management, verification, search credits, and subscriptions.
3. **Tier 3: Super Admin Platform Events (35 to 48)** — Governance and platform-level infrastructure alerts for system administrators covering security, billing failures, provider timeouts, and verification decisions.

---

## 2. User / Company User Events (Events 1 to 20)

| # | Event Key | Category | Priority | Audience | Cooldown | Mandatory | Channels | Description & Deep-Link URL |
| :-: | :--- | :--- | :--- | :--- | :-: | :-: | :---: | :--- |
| **1** | `user.registered` | SECURITY | SUCCESS | USER | 60m | **Yes** | In-App, Push | Welcome notification upon account activation. (`/dashboard/`) |
| **2** | `user.email_verification_requested` | SECURITY | WARNING | USER | 15m | **Yes** | In-App, Push | Email address verification reminder to unlock trading tools. (`/dashboard/`) |
| **3** | `user.password_reset_requested` | SECURITY | WARNING | USER | 5m | **Yes** | In-App, Push | Security notice when a password reset token is requested. (`/login/`) |
| **4** | `user.password_changed` | SECURITY | INFO | USER | 5m | **Yes** | In-App, Push | Security confirmation immediately after password is changed. (`/login/`) |
| **5** | `user.account_status_changed` | SECURITY | WARNING | USER | 15m | **Yes** | In-App, Push | Alert when user account status changes (active, inactive, suspended). (`/help/`) |
| **6** | `company.invitation_received` | SECURITY | INFO | USER | 60m | **Yes** | In-App, Push | Notice to user invited to join a corporate company workspace. (`/company/invitations/`) |
| **7** | `company.member_permission_changed` | SECURITY | INFO | USER | 10m | **Yes** | In-App, Push | Alert to member when company admin updates assigned module roles. (`/company/profile/`) |
| **8** | `requirement.changed` | PROCUREMENT | INFO | CUSTOMER | 15m | No | In-App, Push | Requirement status updated (published, closed, revised). (`/requirements/{id}/`) |
| **9** | `ai.supplier_search.completed` | AI_SEARCH | SUCCESS | CUSTOMER | 5m | No | In-App, Push | AI discovery job finished extracting verified vendor matches. (`/requirements/{id}/suppliers/`) |
| **10** | `ai.buyer_search.completed` | AI_SEARCH | SUCCESS | VENDORS | 5m | No | In-App, Push | AI discovery job finished finding verified customer leads. (`/catalog/products/{id}/buyers/`) |
| **11** | `ai.matches.found` | AI_SEARCH | SUCCESS | USER | 5m | No | In-App, Push | Real-time notification when high-confidence matches are found. (`/requirements/`) |
| **12** | `inquiry.created` | SALES | INFO | CUSTOMER | 5m | No | In-App, Push | Customer confirmation that RFQ / commercial inquiry was sent. (`/leads/inquiries/{id}/`) |
| **13** | `inquiry.message_received` | SALES | INFO | USER | 5m | No | In-App, Push | New reply or message logged on active inquiry discussion thread. (`/leads/inquiries/{id}/`) |
| **14** | `quotation.status_changed` | SALES | INFO | USER | 15m | No | In-App, Push | Commercial quote status change (logged, accepted, declined). (`/leads/inquiries/{id}/`) |
| **15** | `follow_up.reminder_due` | SALES | WARNING | USER | 60m | No | In-App, Push | Reminder when scheduled follow-up on prospective lead is due. (`/leads/saved/`) |
| **16** | `data_transfer.completed` | GENERAL | SUCCESS | USER | 10m | No | In-App, Push | Background CSV/Excel data export or catalog import finished. (`/dashboard/`) |
| **17** | `ai.search.failed` | AI_SEARCH | WARNING | USER | 15m | No | In-App, Push | Notice when search crawler encounters provider error with refund notice. (`/ai-search/history/`) |
| **18** | `credits.threshold_reached` | BILLING | WARNING | USER | 120m | No | In-App, Push | Warning when user/company search credits reach low warning mark. (`/company/billing/`) |
| **19** | `subscription.expiry_reminder` | BILLING | WARNING | USER | 1440m | No | In-App, Push | Advance notice to user when subscription renewal date approaches. (`/company/billing/`) |
| **20** | `billing.payment_confirmed` | BILLING | SUCCESS | USER | 60m | **Yes** | In-App, Push | Commercial receipt confirmation for successful payment. (`/company/billing/invoices/`) |

---

## 3. Company Admin Events (Events 21 to 34)

| # | Event Key | Category | Priority | Audience | Cooldown | Mandatory | Channels | Description & Deep-Link URL |
| :-: | :--- | :--- | :--- | :--- | :-: | :-: | :---: | :--- |
| **21** | `company.member_added` | SECURITY | SUCCESS | COMPANY_ADMINS | 15m | **Yes** | In-App, Push | Alert to admin when a new team member joins the company. (`/company/users/`) |
| **22** | `company.verification_status_changed` | GENERAL | INFO | COMPANY_ADMINS | 60m | **Yes** | In-App, Push | Official badge update when company verification is reviewed. (`/company/profile/`) |
| **23** | `company.invitation_status_changed` | SECURITY | INFO | COMPANY_ADMINS | 15m | No | In-App, Push | Notice when invited user accepts or declines company invitation. (`/company/users/`) |
| **24** | `company.member_permission_changed_admin` | SECURITY | INFO | COMPANY_ADMINS | 10m | **Yes** | In-App, Push | Confirmation when admin updates role or permission set for a user. (`/company/users/`) |
| **25** | `company.member_status_changed` | SECURITY | WARNING | COMPANY_ADMINS | 15m | **Yes** | In-App, Push | Notification when a member account is deactivated or reactivated. (`/company/users/`) |
| **26** | `company.inquiry_received` | SALES | SUCCESS | COMPANY_ADMINS | 5m | No | In-App, Push | New RFQ or procurement inquiry received from customer company. (`/sales/inquiries/`) |
| **27** | `ai.search.repeated_failure` | AI_SEARCH | WARNING | COMPANY_ADMINS | 60m | No | In-App, Push | Alert when discovery queries fail repeatedly within short timeframe. (`/ai-search/history/`) |
| **28** | `company.credits.low` | BILLING | WARNING | COMPANY_ADMINS | 720m | **Yes** | In-App, Push | Urgent warning when company credit balance falls below 15% threshold. (`/company/billing/`) |
| **29** | `company.subscription.status_changed` | BILLING | INFO | COMPANY_ADMINS | 60m | **Yes** | In-App, Push | Notice when company subscription tier upgrades, renews, or lapses. (`/company/billing/`) |
| **30** | `billing.payment_failed` | BILLING | CRITICAL | COMPANY_ADMINS | 30m | **Yes** | In-App, Push | High-priority alert when automated billing or invoice payment fails. (`/company/billing/`) |
| **31** | `billing.invoice_generated` | BILLING | INFO | COMPANY_ADMINS | 60m | No | In-App, Push | Monthly tax invoice generated and ready for PDF download. (`/company/billing/invoices/`) |
| **32** | `company.profile_verification_updated` | GENERAL | INFO | COMPANY_ADMINS | 60m | No | In-App, Push | Corporate compliance or GST verification documents updated. (`/company/profile/`) |
| **33** | `report.scheduled.failed` | GENERAL | WARNING | COMPANY_ADMINS | 120m | No | In-App, Push | Notice when scheduled weekly/monthly PDF procurement report fails. (`/reports/`) |
| **34** | `security.suspicious_user_activity` | SECURITY | CRITICAL | COMPANY_ADMINS | 15m | **Yes** | In-App, Push | Incident alert for repeated failed logins or unauthorized access attempts. (`/company/security/`) |

---

## 4. Super Admin Platform Events (Events 35 to 48)

| # | Event Key | Category | Priority | Audience | Cooldown | Mandatory | Channels | Description & Deep-Link URL |
| :-: | :--- | :--- | :--- | :--- | :-: | :-: | :---: | :--- |
| **35** | `platform.company_registered` | GENERAL | INFO | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Governance notice when a new company enterprise registers. (`/admin-panel/companies/{id}/`) |
| **36** | `platform.company_verification_pending` | GENERAL | WARNING | SUPER_ADMINS | 30m | **Yes** | In-App, Push | KYC verification documents submitted for manual compliance review. (`/admin-panel/companies/{id}/`) |
| **37** | `platform.company_verification_decided` | GENERAL | INFO | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Audit record when verification is approved or revoked. (`/admin-panel/companies/{id}/`) |
| **38** | `platform.user_registered` | GENERAL | INFO | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Platform-wide user registration tracking notice. (`/admin-panel/users/`) |
| **39** | `platform.company_status_changed` | SECURITY | WARNING | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Super Admin alert when a company is suspended or reactivated. (`/admin-panel/companies/{id}/`) |
| **40** | `platform.subscription_status_changed` | BILLING | INFO | SUPER_ADMINS | 30m | **Yes** | In-App, Push | Commercial tracking when a company upgrades/cancels plan. (`/admin-panel/subscriptions/`) |
| **41** | `platform.billing_issue` | BILLING | CRITICAL | SUPER_ADMINS | 30m | **Yes** | In-App, Push | System alert when payment gateway webhook reports recurring failure. (`/admin-panel/billing/`) |
| **42** | `platform.credit_usage_alert` | BILLING | WARNING | SUPER_ADMINS | 120m | **Yes** | In-App, Push | Anomaly alert when a tenant consumes search credits at abnormal velocity. (`/admin-panel/credits/`) |
| **43** | `platform.ai_provider_failure` | AI_SEARCH | CRITICAL | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Upstream AI engine (Gemini/Groq/OpenAI) quota exhaustion or failover. (`/admin-panel/ai-providers/`) |
| **44** | `platform.web_search_failure` | AI_SEARCH | WARNING | SUPER_ADMINS | 30m | **Yes** | In-App, Push | Live web extraction engine or SERP provider connection failure. (`/admin-panel/telemetry/`) |
| **45** | `platform.background_task_failure` | GENERAL | CRITICAL | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Asynchronous celery / background worker queue exception. (`/admin-panel/jobs/`) |
| **46** | `platform.email_delivery_failure` | GENERAL | CRITICAL | SUPER_ADMINS | 30m | **Yes** | In-App, Push | SMTP transport failure after all automated delivery retries exhausted. (`/admin-panel/email-logs/`) |
| **47** | `platform.security_alert` | SECURITY | CRITICAL | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Platform security alert (brute force attack, session hijacking, IP block). (`/admin-panel/audit-logs/`) |
| **48** | `platform.critical_exception` | SECURITY | CRITICAL | SUPER_ADMINS | 15m | **Yes** | In-App, Push | Unhandled platform error or database deadlock requiring intervention. (`/admin-panel/telemetry/`) |

---

## 5. Backward Compatibility Alias Mapping

To ensure older views and background tasks continue to function seamlessly, `get_event_definition()` translates legacy keys automatically:

| Legacy Event Key | Maps to Canonical Event Key | Canonical Description |
| :--- | :--- | :--- |
| `company.user_added` | `company.member_added` | New Member Joined Company |
| `company.registered` | `platform.company_registered` | New Company Workspace Registered |
| `credits.low` | `company.credits.low` | Company Search Credits Running Low |
| `company.verification_updated` | `company.verification_status_changed` | Company Verification Decision |
| `admin.new_company` | `platform.company_registered` | New Company Registered Alert |
| `admin.security_alert` | `platform.security_alert` | Security Threat Threshold Breached |
| `admin.company_suspended` | `platform.company_status_changed` | Company Suspended by Governance |
| `auth.password_reset` | `user.password_reset_requested` | Password Reset Link Requested |
| `auth.password_changed` | `user.password_changed` | Password Update Confirmation |
| `admin.email_delivery_failure`| `platform.email_delivery_failure` | SMTP Delivery Failure Exhausted |
