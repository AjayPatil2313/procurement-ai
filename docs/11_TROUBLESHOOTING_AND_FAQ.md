# Chapter 11: Troubleshooting & Frequently Asked Questions (FAQ)

---

## 11.1 Common Developer & Operational Issues

### 1. MySQL Database Connection Refused
- **Symptom**: `django.db.utils.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1' (10061)")`.
- **Root Cause**: The local MySQL Windows service is stopped, or the port in `.env` is misconfigured.
- **Remediation**:
  ```powershell
  # Check if MySQL service is running
  Get-Service -Name *mysql*

  # Start the MySQL service
  Start-Service -Name MySQL80

  # Verify connectivity on port 3306
  Test-NetConnection -ComputerName 127.0.0.1 -Port 3306
  ```

---

### 2. Upstream AI Rate Limit (HTTP 429)
- **Symptom**: Google Gemini API quota exceeded during peak supplier discovery.
- **Root Cause**: Free-tier or low-tier API key reached the queries-per-minute (QPM) threshold.
- **How TradeConnect Resolves It**:
  - The `AIFailoverGateway` catches the HTTP 429 exception automatically.
  - The circuit breaker engages and routes the query to **OpenAI GPT-4o-mini Backup** with zero manual intervention.
  - The event is logged in `apps.ai_search.models.APILog` with `is_failover=True` and `failover_reason='Gemini HTTP 429 Quota Exceeded'`.
  - The user interface receives valid search results with a subtle indicator showing fallback completion.

---

### 3. SMTP Authentication Failure / Timeout
- **Symptom**: `smtplib.SMTPAuthenticationError: (535, '5.7.8 Username and Password not accepted')` or connection timeout.
- **Root Cause**: Using a standard Google account password rather than a dedicated **16-character Google App Password**, or corporate firewall blocking port 587.
- **Remediation**:
  1. Navigate to Google Account -> Security -> 2-Step Verification -> App Passwords.
  2. Generate a dedicated App Password for "TradeConnect Mailer".
  3. Update `.env`:
     ```ini
     EMAIL_HOST_USER=your_email@gmail.com
     EMAIL_HOST_PASSWORD=abcdefghijklmnop
     ```
  4. Test live connectivity from the Super Admin Panel:
     Navigate to `/admin-panel/email/smtp/` and click **"Test SMTP Connection"**.
  5. For development without live credentials, switch to console output:
     ```ini
     EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
     ```

---

### 4. CSRF Verification Failed (`403 Forbidden`)
- **Symptom**: Form submission displays `CSRF verification failed. Request aborted.`
- **Root Cause**: Missing `{% csrf_token %}` inside custom HTML forms, or mismatched `CSRF_TRUSTED_ORIGINS` when testing behind reverse proxies or ngrok tunnels.
- **Remediation**:
  - Ensure all POST forms in templates include the template tag:
    ```html
    <form method="POST" action="...">
        {% csrf_token %}
        ...
    </form>
    ```
  - If developing via a custom domain or tunneling tool, add the origin to `.env`:
    ```ini
    CSRF_TRUSTED_ORIGINS=https://your-tunnel.ngrok-free.app,http://127.0.0.1:8000
    ```

---

### 5. Suspended User Cannot Access Workspaces
- **Symptom**: User is repeatedly redirected to the login screen with a warning message upon entering credentials.
- **Root Cause**: The user's enterprise organization has been marked `is_suspended=True` by a Super Administrator.
- **Remediation**:
  1. Log in to the Super Admin Console (`/admin-panel/companies/`).
  2. Locate the organization and review the `suspension_reason`.
  3. If resolved, click the three-dot action menu and select **"Reactivate Company"**.
  4. The user will immediately be permitted to log in and resume business workflows.

---

## 11.2 Frequently Asked Questions (FAQ)

#### Q1: Can a company operate as both a Buyer and a Vendor simultaneously?
**A**: Yes. By setting `company_type='BOTH'`, TradeConnect grants access to both Procurement (`/procurement/`) and Sales (`/sales/`) desks. The navigation bar displays a dynamic role toggle enabling users to switch perspectives instantly.

#### Q2: How does TradeConnect prevent accidental email spamming during automated events?
**A**: The notification engine implements three layers of defense:
1. **Idempotency Keys**: Critical events generate unique keys (e.g. `verif_pend_{company_id}_{YYYYMMDD}`) preventing duplicate queueing within the same cycle.
2. **Configurable Cooldown Windows**: High-frequency alerts (such as low credit balance) enforce an automatic cooldown period (default: 24 hours).
3. **Mandatory Security Lockouts**: Authentication emails (`auth.password_reset`, `auth.email_verification`) cannot be accidentally disabled.

#### Q3: Are deleted products and requirements permanently erased?
**A**: No. TradeConnect enforces the enterprise **Soft Delete pattern** (`is_deleted=True`, `deleted_at=timezone.now()`). Records are excluded from active search results and buyer feeds but remain intact in the database for financial compliance, historical quotation audits, and optional Super Admin restoration.

#### Q4: How are credits deducted for AI searches and vendor contact unlocks?
**A**: Credits are debited atomically using Django's `select_for_update()` transaction lock. If an organization does not have sufficient available credits (`total_available < feature_cost`), the action is rejected before contacting third-party AI APIs, protecting the platform from unbilled API usage.
