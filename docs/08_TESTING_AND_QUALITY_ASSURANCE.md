# Chapter 8: Testing & Quality Assurance

---

## 8.1 Testing Philosophy & Framework

TradeConnect employs an automated testing strategy combining unit tests, integration tests, and simulated failure scenarios. The test suite leverages Django's built-in testing framework (`django.test.TestCase` and `django.test.Client`) backed by the live MySQL test database.

### Key Quality Principles:
1. **Never Mock Business Logic**: Database transactions, model validators, foreign key constraints, and multi-tenant queries execute against real MySQL schema tables.
2. **Mock Third-Party Networks Only**: External network dependencies (upstream AI APIs, SMTP mail relays, payment gateways) are mocked using `unittest.mock.patch` to guarantee deterministic, fast test runs.
3. **Zero Test Regressions**: Every major feature addition (such as the 21-event notification management system) must run against existing legacy test suites to ensure backward compatibility.

---

## 8.2 Executing Automated Tests

### 1. Run Complete Notifications App Test Suite (43 Tests)
```powershell
python manage.py test apps.notifications.tests --keepdb
```
*Verified Output:*
```
Ran 43 tests in 163.900s
OK
Preserving test database for alias 'default'...
```

### 2. Run Focused Test Classes
To test specific subsystems without running the entire suite:

```powershell
# Test Centralized Notification Management System (8 tests)
python manage.py test apps.notifications.tests.NotificationManagementSystemTests --keepdb

# Test Standard TradeConnect Master Email Templates (3 tests)
python manage.py test apps.notifications.tests.TwelveStandardEmailTemplatesTests --keepdb

# Test AI Gateway and Cloud Failover Pipeline
python manage.py test apps.ai_search.tests --keepdb

# Test Credit Wallet & Ledger Deductions
python manage.py test apps.billing.tests --keepdb
```

---

## 8.3 Detailed Test Suite Inventory

### `apps.notifications.tests` (43 Verified Tests)
| Test Class | Verified Test Methods | Primary Verification Objective |
| :--- | :--- | :--- |
| `NotificationModelAndEngineTests` | `test_template_rendering`<br>`test_branded_html_builder`<br>`test_trigger_email_notification` | String variable interpolation, 2-card responsive HTML compilation, and outbox creation. |
| `TwelveStandardEmailTemplatesTests` | `test_all_twelve_templates_exist`<br>`test_superadmin_preview_api`<br>`test_company_type_workflow_scoping` | Backward compatibility for legacy templates, master header/trust badges/footer rendering, and company type scoping (`BUYER` vs `SELLER` vs `BOTH`). |
| `NotificationManagementSystemTests` | `test_all_21_notification_events_exist_and_configured`<br>`test_mandatory_auth_rules_cannot_be_disabled`<br>`test_delivery_mode_automatic_blocks_manual_send`<br>`test_delivery_mode_manual_blocks_automatic_event`<br>`test_delivery_mode_both_allows_both`<br>`test_credits_low_cooldown_and_threshold_enforcement`<br>`test_admin_trigger_edit_view_updates_configuration`<br>`test_admin_trigger_preview_and_test_send_api` | All 21 required events seeded; mandatory security rules cannot be deactivated; strict delivery mode contract enforcement; credit threshold and cooldown prevention; and Super Admin preview/test send APIs. |
| `LayoutAndCustomizationTests` | `test_admin_email_layout_view`<br>`test_template_level_header_footer_override`<br>`test_admin_email_logs_export_csv` | Global and template-level header/footer overrides and CSV log export. |

---

## 8.4 Mocking Strategies & Verified Patterns

### 1. Mocking Outgoing SMTP Dispatches
To prevent real email transmission during automated tests while ensuring delivery logic executes:
```python
from unittest.mock import patch, MagicMock
from django.core.mail import EmailMultiAlternatives

# Mock the active connection to simulate successful transmission
with patch("apps.notifications.services.email_engine.get_active_email_connection") as mock_conn:
    mock_backend = MagicMock()
    mock_backend.send_messages.return_value = 1
    mock_conn.return_value = (mock_backend, "noreply@tradeconnect.com")
    
    with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1) as mock_send:
        # Perform test dispatch
        ...
        self.assertEqual(mock_send.call_count, 1)
```

### 2. Mocking Upstream AI Failover (Gemini -> OpenAI)
To test circuit breaker transition when Gemini experiences an HTTP 429 quota exception:
```python
with patch("requests.post") as mock_post:
    def fake_post(url, **kwargs):
        if "generativelanguage.googleapis.com" in url:
            # Simulate Google Gemini HTTP 429 Too Many Requests
            mock_res = MagicMock()
            mock_res.status_code = 429
            mock_res.json.return_value = {"error": "Quota Exceeded"}
            return mock_res
        elif "api.openai.com" in url:
            # Cloud Failover responds 200 OK
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "choices": [{"message": {"content": json.dumps({"matches": []})}}]
            }
            return mock_res
        raise ValueError(f"Unexpected URL: {url}")

    mock_post.side_effect = fake_post
    results, telemetry = AIFailoverGateway.generate("Find valve manufacturers")
    self.assertTrue(telemetry["failover"])
    self.assertEqual(telemetry["engine"], "OpenAI Backup")
```
