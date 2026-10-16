import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.companies.models import Company, CompanyMember
from apps.notifications.models import (
    NotificationTemplate,
    NotificationRule,
    NotificationPreference,
    NotificationLog,
    EmailDeliveryAttempt,
    SMTPSetting,
    EmailLayoutSetting,
)
from apps.notifications.services.email_engine import (
    render_string_template,
    build_branded_html,
    trigger_email_notification,
    deliver_notification_log,
    retry_failed_notification_log,
)
from apps.notifications.services.recipient_resolver import resolve_rule_recipients
from apps.notifications.services.smtp_service import (
    get_active_email_connection,
    test_smtp_connection,
)

User = get_user_model()


class NotificationModelAndEngineTests(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_superuser(
            email="superadmin@example.com",
            password="adminpassword123",
            first_name="Super",
            last_name="Admin",
        )
        self.buyer_user = User.objects.create_user(
            email="buyer@buyercorp.com",
            password="password123",
            first_name="Alice",
            last_name="Buyer",
        )
        self.buyer_company = Company.objects.create(
            name="Buyer Corp",
            company_type=Company.CompanyType.BUYER,
            email="billing@buyercorp.com",
            created_by=self.buyer_user,
        )
        CompanyMember.objects.create(
            company=self.buyer_company,
            user=self.buyer_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Create a test template
        self.template = NotificationTemplate.objects.create(
            event_key="test.event_notice",
            name="Test Notice",
            category=NotificationTemplate.Category.BUYER,
            subject="Alert for {{ recipient_name }}: {{ title }}",
            html_content="<p>Dear {{ recipient_name }}, your item {{ title }} is updated.</p>",
            text_content="Dear {{ recipient_name }}, item {{ title }} updated.",
            variables=["recipient_name", "title"],
            is_active=True,
        )

        # Create a matching rule
        self.rule = NotificationRule.objects.create(
            name="Test Rule",
            event_key="test.event_notice",
            module=NotificationRule.ModuleType.PROCUREMENT,
            company_type=NotificationRule.CompanyTypeScope.ALL,
            template=self.template,
            recipient_type=NotificationRule.RecipientType.ACTING_USER,
            severity=NotificationRule.Severity.INFO,
            priority=NotificationRule.Priority.NORMAL,
            is_enabled=True,
        )

    def test_template_rendering(self):
        rendered = render_string_template("Hello {{ user_name }}!", {"user_name": "Bob"})
        self.assertEqual(rendered, "Hello Bob!")

    def test_branded_html_builder(self):
        html = build_branded_html(
            title="Sourcing Complete",
            summary="Discovered 12 vendors",
            details={"MOQ": "50", "Unit": "pcs"},
            cta_url="https://example.com/item/1",
            cta_text="View Suppliers",
            severity="INFO",
        )
        self.assertIn("Sourcing Complete", html)
        self.assertIn("Discovered 12 vendors", html)
        self.assertIn("View Suppliers", html)
        self.assertIn("TradeConnect", html)
        self.assertIn("Procurement &amp; Sales", html)

    def test_recipient_resolution_acting_user(self):
        recipients = resolve_rule_recipients(self.rule, user=self.buyer_user, company=self.buyer_company)
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0]["email"], self.buyer_user.email)

    def test_recipient_resolution_company_admins(self):
        self.rule.recipient_type = NotificationRule.RecipientType.COMPANY_ADMINS
        self.rule.save()
        recipients = resolve_rule_recipients(self.rule, company=self.buyer_company)
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0]["email"], self.buyer_user.email)

    def test_recipient_resolution_billing_contacts(self):
        self.rule.recipient_type = NotificationRule.RecipientType.BILLING_CONTACTS
        self.rule.save()
        recipients = resolve_rule_recipients(self.rule, company=self.buyer_company)
        emails = [r["email"] for r in recipients]
        self.assertIn("billing@buyercorp.com", emails)

    def test_user_notification_preference_opt_out(self):
        # User opts out of PROCUREMENT category
        NotificationPreference.objects.create(
            user=self.buyer_user,
            company=self.buyer_company,
            category=NotificationPreference.Category.PROCUREMENT,
            email_enabled=False,
        )
        # Should be filtered out
        recipients = resolve_rule_recipients(self.rule, user=self.buyer_user, company=self.buyer_company)
        self.assertEqual(len(recipients), 0)

    def test_user_notification_preference_mandatory_security_cannot_be_blocked(self):
        # User tries to opt out of SECURITY category
        NotificationPreference.objects.create(
            user=self.buyer_user,
            company=self.buyer_company,
            category=NotificationPreference.Category.SECURITY,
            email_enabled=False,
        )
        # Rule in AUTH module
        self.rule.module = NotificationRule.ModuleType.AUTH
        self.rule.save()
        # is_email_permitted MUST return True for SECURITY
        self.assertTrue(NotificationPreference.is_email_permitted(self.buyer_user, NotificationPreference.Category.SECURITY))
        recipients = resolve_rule_recipients(self.rule, user=self.buyer_user, company=self.buyer_company)
        self.assertEqual(len(recipients), 1)

    @patch("apps.notifications.services.email_engine.deliver_notification_log")
    def test_trigger_email_notification_creates_log(self, mock_deliver):
        mock_deliver.return_value = True
        logs = trigger_email_notification(
            event_key="test.event_notice",
            company=self.buyer_company,
            user=self.buyer_user,
            context={"title": "Widget A"},
            async_dispatch=False,
        )
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log.recipient_email, self.buyer_user.email)
        self.assertEqual(log.event_key, "test.event_notice")
        self.assertIn("Widget A", log.subject)

    def test_idempotency_prevents_duplicate_dispatch(self):
        idempotency_key = "test_unique_key_123"
        # First call
        logs_1 = trigger_email_notification(
            event_key="test.event_notice",
            company=self.buyer_company,
            user=self.buyer_user,
            context={"title": "Widget 1"},
            idempotency_key=idempotency_key,
            async_dispatch=False,
        )
        self.assertEqual(len(logs_1), 1)

        # Second call with same idempotency_key must be skipped
        logs_2 = trigger_email_notification(
            event_key="test.event_notice",
            company=self.buyer_company,
            user=self.buyer_user,
            context={"title": "Widget 1 Duplicate"},
            idempotency_key=idempotency_key,
            async_dispatch=False,
        )
        self.assertEqual(len(logs_2), 0)

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_deliver_notification_log_success(self, mock_send):
        mock_send.return_value = 1
        log = NotificationLog.objects.create(
            event_key="test.event_notice",
            template=self.template,
            recipient_email="test@example.com",
            subject="Test Subject",
            html_body="<p>Test</p>",
            text_body="Test",
        )
        success = deliver_notification_log(log.id)
        self.assertTrue(success)
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertEqual(log.attempts_count, 1)
        self.assertEqual(log.attempts.count(), 1)
        self.assertEqual(log.attempts.first().status, EmailDeliveryAttempt.Status.SUCCESS)

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_deliver_notification_log_failure_and_retry(self, mock_send):
        # First send fails
        mock_send.side_effect = Exception("SMTP Connection Timeout")
        log = NotificationLog.objects.create(
            event_key="test.event_notice",
            template=self.template,
            recipient_email="test@example.com",
            subject="Test Subject",
            html_body="<p>Test</p>",
            text_body="Test",
            max_attempts=1,
        )
        deliver_notification_log(log.id)
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.FAILED)
        self.assertIn("SMTP Connection Timeout", log.last_error)

        # Now mock succeeds on retry
        mock_send.side_effect = None
        mock_send.return_value = 1
        retry_success = retry_failed_notification_log(log, async_dispatch=False)
        self.assertTrue(retry_success)
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertEqual(log.attempts_count, 2)


class SuperAdminEmailViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.super_admin = User.objects.create_superuser(
            email="superadmin@platform.com",
            password="adminpassword123",
            first_name="Admin",
            last_name="Super",
        )
        self.regular_user = User.objects.create_user(
            email="user@example.com",
            password="userpassword123",
            first_name="Regular",
            last_name="User",
        )
        self.template = NotificationTemplate.objects.create(
            event_key="buyer.test_action",
            name="Action Template",
            category=NotificationTemplate.Category.BUYER,
            subject="Action for {{ recipient_name }}",
            html_content="<p>Action required.</p>",
            is_active=True,
        )
        self.rule = NotificationRule.objects.create(
            name="Action Rule",
            event_key="buyer.test_action",
            module=NotificationRule.ModuleType.PROCUREMENT,
            template=self.template,
            recipient_type=NotificationRule.RecipientType.ACTING_USER,
            is_enabled=True,
        )

    def test_non_superadmin_forbidden(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse("admin-panel-email-overview"))
        self.assertEqual(response.status_code, 403)

    def test_superadmin_overview_view(self):
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("admin-panel-email-overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email Notification Operations")
        self.assertContains(response, "Delivered / Accepted")

    def test_superadmin_templates_view(self):
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("admin-panel-email-templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Action Template")
        self.assertContains(response, "buyer.test_action")

    def test_superadmin_template_edit_view(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(
            reverse("admin-panel-email-template-edit", kwargs={"pk": self.template.pk}),
            {
                "subject": "Updated Subject Line",
                "html_content": "<p>Updated body content.</p>",
                "text_content": "Updated plain text.",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.assertEqual(self.template.subject, "Updated Subject Line")
        self.assertEqual(self.template.version, 2)

    def test_superadmin_template_preview_api(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(
            reverse("admin-panel-email-template-preview", kwargs={"pk": self.template.pk}),
            {},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("Action for", data["subject"])
        self.assertIn("html", data)

    def test_superadmin_trigger_toggle_view(self):
        self.client.force_login(self.super_admin)
        self.assertTrue(self.rule.is_enabled)
        response = self.client.post(
            reverse("admin-panel-email-trigger-toggle", kwargs={"pk": self.rule.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.rule.refresh_from_db()
        self.assertFalse(self.rule.is_enabled)

    def test_superadmin_smtp_view_and_save(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(
            reverse("admin-panel-email-smtp"),
            {
                "backend_type": "SMTP",
                "sender_name": "Procurement Mailer",
                "sender_email": "mailer@procurement.ai",
                "host": "smtp.sendgrid.net",
                "port": "587",
                "use_tls": "on",
                "username": "apikey",
                "password": "secret_smtp_password",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        smtp = SMTPSetting.get_active_config()
        self.assertIsNotNone(smtp)
        self.assertEqual(smtp.host, "smtp.sendgrid.net")
        self.assertTrue(smtp.is_active)

    def test_superadmin_logs_view(self):
        log = NotificationLog.objects.create(
            event_key="buyer.test_action",
            template=self.template,
            recipient_email="client@example.com",
            subject="Client Notification",
            status=NotificationLog.Status.SENT,
        )
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("admin-panel-email-logs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "client@example.com")

    def test_superadmin_log_detail_api(self):
        log = NotificationLog.objects.create(
            event_key="buyer.test_action",
            template=self.template,
            recipient_email="client@example.com",
            subject="Client Notification",
            html_body="<b>Rendered HTML</b>",
            status=NotificationLog.Status.SENT,
        )
        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("admin-panel-email-log-detail", kwargs={"pk": log.pk})
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["recipient_email"], "client@example.com")
        self.assertEqual(data["html_body"], "<b>Rendered HTML</b>")


class UserPreferencesViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="member@enterprise.com",
            password="memberpassword123",
            first_name="John",
            last_name="Doe",
        )
        self.company = Company.objects.create(
            name="Enterprise Ltd",
            company_type=Company.CompanyType.BOTH,
            created_by=self.user,
        )
        CompanyMember.objects.create(
            company=self.company,
            user=self.user,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

    def test_user_preferences_page_render(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

        response = self.client.get(reverse("user-notification-preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notification Preferences")
        self.assertContains(response, "Procurement &amp; Sourcing Updates")

    def test_user_preferences_post_update(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

        # Disable MARKETING email
        response = self.client.post(
            reverse("user-notification-preferences"),
            {
                "email_PROCUREMENT": "on",
                "in_app_PROCUREMENT": "on",
                "email_SALES": "on",
                "in_app_SALES": "on",
                # MARKETING email omitted -> false
            },
        )
        self.assertEqual(response.status_code, 302)
        pref_mkt = NotificationPreference.objects.filter(
            user=self.user,
            category=NotificationPreference.Category.MARKETING,
        ).first()
        self.assertIsNotNone(pref_mkt)
        self.assertFalse(pref_mkt.email_enabled)


class TwelveStandardEmailTemplatesTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_email_templates")

        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            email="admin@tradeconnect.com",
            password="adminpassword123",
            first_name="Super",
            last_name="Admin",
        )
        self.buyer_user = User.objects.create_user(
            email="buyer@enterprise.com",
            password="password123",
            first_name="Buyer",
            last_name="User",
        )
        self.buyer_company = Company.objects.create(
            name="Alpha Procurement Ltd",
            company_type=Company.CompanyType.BUYER,
            email="buyer@enterprise.com",
            created_by=self.buyer_user,
        )
        CompanyMember.objects.create(
            company=self.buyer_company,
            user=self.buyer_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.seller_user = User.objects.create_user(
            email="seller@enterprise.com",
            password="password123",
            first_name="Seller",
            last_name="User",
        )
        self.seller_company = Company.objects.create(
            name="Beta Manufacturing Ltd",
            company_type=Company.CompanyType.SELLER,
            email="seller@enterprise.com",
            created_by=self.seller_user,
        )
        CompanyMember.objects.create(
            company=self.seller_company,
            user=self.seller_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.both_user = User.objects.create_user(
            email="both@enterprise.com",
            password="password123",
            first_name="Both",
            last_name="User",
        )
        self.both_company = Company.objects.create(
            name="Gamma Global Corp",
            company_type=Company.CompanyType.BOTH,
            email="both@enterprise.com",
            created_by=self.both_user,
        )
        CompanyMember.objects.create(
            company=self.both_company,
            user=self.both_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

    def test_all_twelve_templates_exist_and_render_identically(self):
        """
        Validates that all 12 requested templates exist in the database,
        and render with the exact standard TradeConnect master layout:
        1. Fixed Master Header
        2. Event-specific Hero Banner
        3. Dynamic Body with Card layout
        4. Fixed 3-column Trust Badges
        5. Fixed Dark Footer
        """
        required_templates = [
            "seller.inquiry_received",          # 1. New inquiry received
            "buyer.matching_suppliers_found",   # 2. Matching suppliers found
            "seller.new_buyer_lead",            # 3. New buyer lead
            "billing.wallet_recharge_success",  # 4. Wallet recharge successful
            "billing.invoice_generated",        # 5. Invoice generated
            "search.ai_completed",              # 6. AI search completed
            "auth.password_reset",              # 7. Password reset
            "admin.critical_system_alert",      # 8. Critical system alert
            "billing.low_wallet_balance",       # 9. Low wallet balance
            "billing.subscription_expiring",    # 10. Subscription expiring
            "admin.company_registered",         # 11. New company registration
            "reports.export_ready",             # 12. Export report ready
        ]

        sample_context = {
            "recipient_name": "Valued Enterprise",
            "buyer_company": "Acme Industries",
            "product_name": "Heavy Valves",
            "quantity": "500 Units",
            "requirement_title": "Forged Valves",
            "count": "12",
            "amount": "15,000.00",
            "transaction_ref": "TXN-12345",
            "new_balance": "25,000.00",
            "invoice_number": "INV-2026-09",
            "query_title": "Valves Manufacturers",
            "user_name": "Admin User",
            "incident_title": "Latency Spike",
            "service_name": "Embedding API",
            "current_balance": "300.00",
            "plan_name": "Pro Tier",
            "days_remaining": "3",
            "company_name": "Acme Ltd",
            "company_type": "Vendor & Sales",
            "report_name": "Supplier Report",
            "record_count": "50",
            "cta_url": "https://tradeconnect.com/dashboard/",
        }

        for event_key in required_templates:
            tpl = NotificationTemplate.objects.filter(event_key=event_key).first()
            self.assertIsNotNone(tpl, f"Template for '{event_key}' must exist.")
            self.assertTrue(tpl.is_active, f"Template '{event_key}' should be active.")

            subj = render_string_template(tpl.subject, sample_context)
            body = render_string_template(tpl.html_content, sample_context)
            final_html = build_branded_html(title=subj, custom_content=body, event_key=event_key)

            # Master Header check
            self.assertIn("TradeConnect", final_html)
            self.assertTrue("Buy &bull; Sell &bull; Grow" in final_html or "Buy • Sell • Grow" in final_html)
            self.assertIn("B2B Procurement &amp; Sales Platform", final_html)

            # Trust Badges check
            self.assertIn("Secure &amp; Verified", final_html)
            self.assertIn("Trusted B2B Platform", final_html)
            self.assertIn("24/7 Support", final_html)

            # Dark Footer check
            self.assertIn("support@tradeconnect.com", final_html)
            self.assertIn("www.tradeconnect.com", final_html)

            # Dynamic Card body check
            self.assertIn("Next Step", final_html)

    def test_superadmin_preview_api_all_twelve_templates(self):
        """
        Tests the Super Admin preview API endpoint for all 12 templates.
        Ensures the JSON response returns status='success', subject, and rendered HTML.
        """
        self.client.force_login(self.superadmin)

        templates = NotificationTemplate.objects.filter(
            event_key__in=[
                "seller.inquiry_received",
                "buyer.matching_suppliers_found",
                "seller.new_buyer_lead",
                "billing.wallet_recharge_success",
                "billing.invoice_generated",
                "search.ai_completed",
                "auth.password_reset",
                "admin.critical_system_alert",
                "billing.low_wallet_balance",
                "billing.subscription_expiring",
                "admin.company_registered",
                "reports.export_ready",
            ]
        )
        self.assertEqual(templates.count(), 12)

        for tpl in templates:
            url = reverse("admin-panel-email-template-preview", kwargs={"pk": tpl.pk})
            resp = self.client.post(url)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertTrue(len(data["subject"]) > 0)
            self.assertIn("TradeConnect", data["html"])
            self.assertIn("Trusted B2B Platform", data["html"])

    def test_company_type_workflow_scoping(self):
        """
        Verifies that:
        - Buyer companies receive Buyer events.
        - Seller companies receive Seller events.
        - BOTH companies receive both workflows.
        - Super Admins receive admin platform alerts.
        """
        # 1. Trigger buyer event for Buyer company -> should log
        buyer_logs = trigger_email_notification(
            event_key="buyer.matching_suppliers_found",
            company=self.buyer_company,
            user=self.buyer_user,
            context={"count": "5", "requirement_title": "Steel Bars"},
            async_dispatch=False,
        )
        self.assertTrue(len(buyer_logs) >= 1)
        self.assertEqual(buyer_logs[0].recipient_email, self.buyer_user.email)

        # 2. Trigger seller event for Seller company -> should log
        seller_logs = trigger_email_notification(
            event_key="seller.new_buyer_lead",
            company=self.seller_company,
            user=self.seller_user,
            context={"buyer_company": "Test Buyer", "requirement_title": "Pumps"},
            async_dispatch=False,
        )
        self.assertTrue(len(seller_logs) >= 1)
        self.assertEqual(seller_logs[0].recipient_email, self.seller_user.email)

        # 3. BOTH company can receive both buyer and seller events
        both_buyer_logs = trigger_email_notification(
            event_key="buyer.matching_suppliers_found",
            company=self.both_company,
            user=self.both_user,
            context={"count": "8", "requirement_title": "Copper Tubes"},
            async_dispatch=False,
        )
        self.assertTrue(len(both_buyer_logs) >= 1)

        both_seller_logs = trigger_email_notification(
            event_key="seller.new_buyer_lead",
            company=self.both_company,
            user=self.both_user,
            context={"buyer_company": "Delta Corp", "requirement_title": "Copper Tubes"},
            async_dispatch=False,
        )
        self.assertTrue(len(both_seller_logs) >= 1)

        # 4. Super Admin receives critical system alert
        admin_logs = trigger_email_notification(
            event_key="admin.critical_system_alert",
            context={
                "incident_title": "Database Connection Pool Limit",
                "service_name": "MySQL Cluster",
            },
            async_dispatch=False,
        )
        self.assertTrue(len(admin_logs) >= 1)
        self.assertEqual(admin_logs[0].recipient_email, self.superadmin.email)


class EnhancedEmailFeaturesTests(TestCase):
    """
    Unit and integration tests for Deliverability, RFC 8058 headers,
    attachments pipeline, process_email_outbox worker, and SMTP encryption.
    """

    def setUp(self):
        from apps.notifications.management.commands.seed_email_templates import Command as SeedCommand
        SeedCommand().handle()

        self.user = User.objects.create_user(
            email="techbuyer@enterprise.com",
            password="password123",
            first_name="Tech",
            last_name="Buyer",
        )
        self.company = Company.objects.create(
            name="Tech Buyer Enterprise",
            company_type=Company.CompanyType.BUYER,
            email="techbuyer@enterprise.com",
            created_by=self.user,
        )
        self.superadmin = User.objects.create_superuser(
            email="superadmin_test@platform.com",
            password="superpassword123",
            first_name="Root",
            last_name="SuperAdmin",
        )
        self.client = Client()

    def test_ensure_absolute_url(self):
        from apps.notifications.services.email_engine import ensure_absolute_url

        self.assertEqual(ensure_absolute_url("https://example.com/test"), "https://example.com/test")
        rel_url = ensure_absolute_url("/company/billing/")
        self.assertTrue(rel_url.startswith("http"))
        self.assertTrue(rel_url.endswith("/company/billing/"))

    def test_tokenized_unsubscribe_and_rfc8058(self):
        from apps.notifications.services.unsubscribe_service import (
            generate_unsubscribe_token,
            verify_unsubscribe_token,
        )

        token = generate_unsubscribe_token(self.user.id, "MARKETING")
        user, category, is_valid, err = verify_unsubscribe_token(token)
        self.assertTrue(is_valid)
        self.assertEqual(user.id, self.user.id)
        self.assertEqual(category, "MARKETING")

        # GET request to public unsubscribe page
        url = reverse("notifications-unsubscribe") + f"?token={token}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.email)

        # RFC 8058 POST One-Click
        response_post = self.client.post(
            reverse("notifications-unsubscribe"),
            {"token": token, "List-Unsubscribe": "One-Click"},
        )
        self.assertEqual(response_post.status_code, 200)

        # Verify preference was set to False
        pref = NotificationPreference.objects.filter(user=self.user, category="MARKETING").first()
        self.assertIsNotNone(pref)
        self.assertFalse(pref.email_enabled)

    def test_email_attachments_pipeline(self):
        from django.core import mail
        mail.outbox.clear()

        pdf_bytes = b"%PDF-1.4 Mock Vector PDF Document"
        attachments = [("Tax_Invoice_INV-001.pdf", pdf_bytes, "application/pdf")]

        logs = trigger_email_notification(
            event_key="billing.invoice_generated",
            company=self.company,
            user=self.user,
            context={
                "invoice_number": "INV-001",
                "total_amount": "5000.00",
                "currency": "INR",
            },
            attachments=attachments,
            async_dispatch=False,
        )
        self.assertTrue(len(logs) >= 1)
        log = logs[0]
        self.assertTrue(log.has_attachments)
        self.assertIn("Tax_Invoice_INV-001.pdf", log.attachment_names)

        # Verify MIME attachment in outbox
        self.assertTrue(len(mail.outbox) >= 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(len(sent_email.attachments), 1)
        self.assertEqual(sent_email.attachments[0][0], "Tax_Invoice_INV-001.pdf")
        self.assertEqual(sent_email.attachments[0][1], pdf_bytes)

    def test_rfc8058_headers_in_delivery(self):
        from django.core import mail
        mail.outbox.clear()

        logs = trigger_email_notification(
            event_key="auth.password_reset",
            user=self.user,
            context={"reset_url": "https://tradeconnect.com/reset-password/?token=abc"},
            async_dispatch=False,
        )
        self.assertTrue(len(logs) >= 1)
        self.assertTrue(len(mail.outbox) >= 1)
        sent_email = mail.outbox[-1]

        self.assertIn("List-Unsubscribe", sent_email.extra_headers)
        self.assertEqual(sent_email.extra_headers.get("List-Unsubscribe-Post"), "List-Unsubscribe=One-Click")
        self.assertTrue(sent_email.extra_headers.get("X-Entity-Ref-ID", "").startswith("TC-NOTIF-"))

    def test_process_email_outbox_command(self):
        from django.core.management import call_command
        from django.core import mail
        mail.outbox.clear()

        # Create a queued notification log
        log = NotificationLog.objects.create(
            event_key="billing.wallet_recharge_success",
            company=self.company,
            user=self.user,
            recipient_email=self.user.email,
            subject="Wallet Recharged",
            html_body="<p>Wallet recharged successfully.</p>",
            text_body="Wallet recharged successfully.",
            status=NotificationLog.Status.QUEUED,
        )

        call_command("process_email_outbox", batch_size=10)
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.SENT)
        self.assertIsNotNone(log.sent_at)
        self.assertEqual(log.attempts_count, 1)

    def test_smtp_setting_password_encryption(self):
        smtp = SMTPSetting.objects.create(
            sender_name="TradeConnect System",
            sender_email="system@tradeconnect.com",
            host="smtp.sendgrid.net",
            port=587,
        )
        smtp.set_password("SuperSecretSmtpApiKey123")
        smtp.save()

        # Database column should not be plain text
        self.assertNotEqual(smtp.password_encrypted, "SuperSecretSmtpApiKey123")
        # Decrypted method retrieves original password
        self.assertEqual(smtp.get_password(), "SuperSecretSmtpApiKey123")

    def test_bulk_retry_all_failed_view(self):
        failed_log = NotificationLog.objects.create(
            event_key="billing.low_wallet_balance",
            company=self.company,
            user=self.user,
            recipient_email=self.user.email,
            subject="Low Balance",
            html_body="<p>Low balance.</p>",
            text_body="Low balance.",
            status=NotificationLog.Status.FAILED,
            attempts_count=3,
            max_attempts=3,
        )

        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("admin-panel-email-logs-retry-all"), {"hours": 24})
        self.assertEqual(response.status_code, 302)

        failed_log.refresh_from_db()
        self.assertEqual(failed_log.status, NotificationLog.Status.QUEUED)
        self.assertTrue(failed_log.max_attempts > 3)

    def test_email_layout_setting_and_ui_view(self):
        """Super Admin can manage Master Email Header & Footer layout through UI."""
        self.client.force_login(self.superadmin)

        # GET layout settings view
        response = self.client.get(reverse("admin-panel-email-layout"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Master Header &amp; Footer Layout")

        # POST update custom branding in header and footer
        post_data = {
            "action": "save",
            "brand_name": "Acme Global B2B",
            "brand_tagline": "Enterprise Sourcing",
            "header_title": "Custom Procurement Portal",
            "header_subtitle": "Intelligent Sourcing. Accelerated Growth.",
            "logo_url": "https://example.com/logo.png",
            "show_trust_badges": "on",
            "badge_1_icon": "🔒",
            "badge_1_text": "Bank-Grade Encryption",
            "badge_2_icon": "⭐",
            "badge_2_text": "Top Rated Platform",
            "badge_3_icon": "📞",
            "badge_3_text": "VIP Support",
            "footer_brand_name": "Acme Global B2B",
            "website_url": "https://acmeglobal.com",
            "support_email": "help@acmeglobal.com",
            "compliance_text": "Custom legal compliance text for Acme Global.",
            "copyright_text": "© 2026 Acme Global B2B Corp.",
            "social_linkedin": "https://linkedin.com/company/acme",
            "social_facebook": "https://facebook.com/acme",
            "social_youtube": "https://youtube.com/acme",
            "social_twitter": "https://twitter.com/acme",
        }
        post_res = self.client.post(reverse("admin-panel-email-layout"), post_data)
        self.assertEqual(post_res.status_code, 302)

        layout = EmailLayoutSetting.get_active_layout()
        self.assertEqual(layout.brand_name, "Acme Global B2B")
        self.assertEqual(layout.support_email, "help@acmeglobal.com")

        # Verify build_branded_html renders the customized header and footer
        rendered = build_branded_html(
            title="Test Inquiry",
            summary="Test summary text",
            layout_setting=layout,
        )
        self.assertIn("Acme Global B2B", rendered)
        self.assertIn("help@acmeglobal.com", rendered)
        self.assertIn("Bank-Grade Encryption", rendered)
        self.assertIn("Custom legal compliance text", rendered)

    def test_template_level_header_footer_override(self):
        """Template editor supports overriding header and footer for a specific template."""
        self.client.force_login(self.superadmin)
        tpl = NotificationTemplate.objects.create(
            name="Custom Layout Notice",
            event_key="test.custom_header_footer",
            category=NotificationTemplate.Category.SYSTEM,
            subject="Notice Subject",
            html_content="<p>Body Content</p>",
        )

        edit_data = {
            "subject": "Notice Subject Updated",
            "html_content": "<p>Updated Body Content</p>",
            "text_content": "Text body",
            "is_active": "on",
            "use_custom_layout": "on",
            "header_html": "<tr><td style='background:#111827;color:#fff;'>Custom Template Header</td></tr>",
            "footer_html": "<tr><td style='background:#111827;color:#fff;'>Custom Template Footer</td></tr>",
        }
        res = self.client.post(reverse("admin-panel-email-template-edit", kwargs={"pk": tpl.pk}), edit_data)
        self.assertEqual(res.status_code, 302)

        tpl.refresh_from_db()
        self.assertTrue(tpl.use_custom_layout)
        self.assertIn("Custom Template Header", tpl.header_html)
        self.assertIn("Custom Template Footer", tpl.footer_html)

        # Preview API reflects custom header and footer
        preview_res = self.client.post(reverse("admin-panel-email-template-preview", kwargs={"pk": tpl.pk}))
        self.assertEqual(preview_res.status_code, 200)
        data = preview_res.json()
        self.assertIn("Custom Template Header", data["html"])
        self.assertIn("Custom Template Footer", data["html"])

    def test_admin_email_logs_export_csv(self):
        """Validates that Super Admin can export email notification logs as CSV."""
        self.client.force_login(self.superadmin)
        res = self.client.get(reverse("admin-panel-email-logs") + "?export=csv")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res["Content-Type"])
        self.assertIn("Email_Delivery_Logs_", res["Content-Disposition"])


class NotificationManagementSystemTests(TestCase):
    """
    Comprehensive tests for the Centralized Notification Management System:
    1. 21 Standardized Events across User, Company Admin, and Super Admin categories.
    2. Delivery Modes: Automatic, Manual, Both.
    3. Mandatory Auth Rules Lock (Cannot disable password_reset / email_verification).
    4. Thresholds & Cooldown Rules (Credits Low).
    5. Super Admin Web Views: Configure, Preview, Test Send, Manual Send.
    """

    def setUp(self):
        from django.core.management import call_command
        call_command("seed_email_templates")

        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            email="superadmin@tradeconnect.internal",
            password="adminpassword123",
            first_name="Super",
            last_name="Admin",
        )
        self.company_admin_user = User.objects.create_user(
            email="ceo@acme.com",
            password="password123",
            first_name="Alice",
            last_name="Director",
        )
        self.company = Company.objects.create(
            name="Acme Industrial Corp",
            company_type=Company.CompanyType.BOTH,
            email="contact@acme.com",
            created_by=self.company_admin_user,
        )
        CompanyMember.objects.create(
            company=self.company,
            user=self.company_admin_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        self.regular_user = User.objects.create_user(
            email="staff@acme.com",
            password="password123",
            first_name="Bob",
            last_name="Operator",
        )
        CompanyMember.objects.create(
            company=self.company,
            user=self.regular_user,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

    def test_all_21_notification_events_exist_and_configured(self):
        """Verifies that all 21 requested events across User, Company Admin, and Super Admin exist with correct audience."""
        expected_events = {
            # USER / COMPANY USER (6)
            "auth.welcome": ("USER", "AUTOMATIC"),
            "auth.email_verification": ("USER", "AUTOMATIC"),
            "auth.password_reset": ("USER", "AUTOMATIC"),
            "auth.password_changed": ("USER", "AUTOMATIC"),
            "user.activation_deactivation": ("USER", "BOTH"),
            "billing.subscription_expiry_user": ("USER", "BOTH"),
            # COMPANY ADMIN (6)
            "company.user_added": ("COMPANY_ADMIN", "AUTOMATIC"),
            "company.verification_decision": ("COMPANY_ADMIN", "BOTH"),
            "billing.credits_low": ("COMPANY_ADMIN", "AUTOMATIC"),
            "billing.subscription_expiring_admin": ("COMPANY_ADMIN", "BOTH"),
            "billing.payment_failed": ("COMPANY_ADMIN", "BOTH"),
            "company.profile_updated": ("COMPANY_ADMIN", "BOTH"),
            # SUPER ADMIN (9)
            "admin.new_company": ("SUPER_ADMIN", "AUTOMATIC"),
            "admin.verification_pending": ("SUPER_ADMIN", "AUTOMATIC"),
            "admin.verification_decision": ("SUPER_ADMIN", "BOTH"),
            "admin.new_user": ("SUPER_ADMIN", "AUTOMATIC"),
            "admin.company_suspended": ("SUPER_ADMIN", "BOTH"),
            "admin.subscription_change": ("SUPER_ADMIN", "BOTH"),
            "admin.payment_issue": ("SUPER_ADMIN", "BOTH"),
            "admin.email_delivery_failure": ("SUPER_ADMIN", "AUTOMATIC"),
            "admin.security_alert": ("SUPER_ADMIN", "AUTOMATIC"),
        }

        for event_key, (expected_aud, expected_mode) in expected_events.items():
            rule = NotificationRule.objects.filter(event_key=event_key).first()
            self.assertIsNotNone(rule, f"Rule for {event_key} must exist.")
            self.assertEqual(rule.target_audience, expected_aud, f"Rule {event_key} audience mismatch.")
            self.assertEqual(rule.delivery_mode, expected_mode, f"Rule {event_key} default mode mismatch.")
            self.assertTrue(rule.is_enabled, f"Rule {event_key} should be enabled by default.")

    def test_mandatory_auth_rules_cannot_be_disabled(self):
        """Mandatory authentication rules (password reset & verification) cannot be disabled via toggle API."""
        self.client.force_login(self.superadmin)
        pw_reset_rule = NotificationRule.objects.get(event_key="auth.password_reset")
        self.assertTrue(pw_reset_rule.is_mandatory_auth)

        # 1. Attempt toggle via AJAX POST -> returns 400 JsonResponse
        url = reverse("admin-panel-email-trigger-toggle", kwargs={"pk": pw_reset_rule.pk})
        res = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("mandatory authentication", data["message"].lower())

        # 2. Attempt toggle via standard form POST -> redirects with 302 and keeps enabled
        std_res = self.client.post(url)
        self.assertEqual(std_res.status_code, 302)

        pw_reset_rule.refresh_from_db()
        self.assertTrue(pw_reset_rule.is_enabled)

    def test_delivery_mode_automatic_blocks_manual_send(self):
        """An AUTOMATIC event skips manual dispatch when requested manually."""
        rule = NotificationRule.objects.get(event_key="auth.welcome")
        self.assertEqual(rule.delivery_mode, NotificationRule.DeliveryMode.AUTOMATIC)

        logs = trigger_email_notification(
            event_key="auth.welcome",
            company=self.company,
            user=self.regular_user,
            is_manual=True,
            sent_by=self.superadmin,
            async_dispatch=False,
        )
        self.assertEqual(len(logs), 0, "Manual send on AUTOMATIC-only event must be blocked.")

    def test_delivery_mode_manual_blocks_automatic_event(self):
        """A MANUAL-configured event skips automatic business event triggers."""
        rule = NotificationRule.objects.get(event_key="company.user_added")
        rule.delivery_mode = NotificationRule.DeliveryMode.MANUAL
        rule.save()

        # Automatic trigger attempt
        logs = trigger_email_notification(
            event_key="company.user_added",
            company=self.company,
            user=self.regular_user,
            is_manual=False,
            async_dispatch=False,
        )
        self.assertEqual(len(logs), 0, "Automatic dispatch on MANUAL-only event must be skipped.")

    def test_delivery_mode_both_allows_both(self):
        """A BOTH-configured event allows both automatic dispatch and manual resend with audit trail."""
        rule = NotificationRule.objects.get(event_key="company.verification_decision")
        self.assertEqual(rule.delivery_mode, NotificationRule.DeliveryMode.BOTH)

        # 1. Automatic trigger
        auto_logs = trigger_email_notification(
            event_key="company.verification_decision",
            company=self.company,
            user=self.company_admin_user,
            context={"status": "Approved", "decision_badge": "Verified"},
            is_manual=False,
            async_dispatch=False,
        )
        self.assertEqual(len(auto_logs), 1)
        self.assertEqual(auto_logs[0].delivery_mode, "AUTOMATIC")

        # 2. Manual resend by Super Admin
        manual_logs = trigger_email_notification(
            event_key="company.verification_decision",
            company=self.company,
            user=self.company_admin_user,
            context={"status": "Approved", "decision_badge": "Verified"},
            is_manual=True,
            sent_by=self.superadmin,
            async_dispatch=False,
        )
        self.assertEqual(len(manual_logs), 1)
        self.assertEqual(manual_logs[0].delivery_mode, "MANUAL")
        self.assertEqual(manual_logs[0].sent_by, self.superadmin)

    def test_credits_low_cooldown_and_threshold_enforcement(self):
        """Validates credit threshold check and cooldown window enforcement for billing.credits_low."""
        from decimal import Decimal
        rule = NotificationRule.objects.get(event_key="billing.credits_low")
        rule.threshold_value = Decimal("500.00")
        rule.cooldown_hours = 24
        rule.save()

        # 1. Balance above threshold -> should NOT trigger
        logs_high = trigger_email_notification(
            event_key="billing.credits_low",
            company=self.company,
            user=self.company_admin_user,
            context={"current_balance": "800.00", "threshold_value": "500.00"},
            async_dispatch=False,
        )
        self.assertEqual(len(logs_high), 0)

        # 2. Balance below threshold -> triggers email log
        logs_low = trigger_email_notification(
            event_key="billing.credits_low",
            company=self.company,
            user=self.company_admin_user,
            context={"current_balance": "350.00", "threshold_value": "500.00"},
            async_dispatch=False,
        )
        self.assertTrue(len(logs_low) >= 1)

        # 3. Repeat immediately while inside cooldown window -> skipped
        logs_cooldown = trigger_email_notification(
            event_key="billing.credits_low",
            company=self.company,
            user=self.company_admin_user,
            context={"current_balance": "320.00", "threshold_value": "500.00"},
            async_dispatch=False,
        )
        self.assertEqual(len(logs_cooldown), 0)

    def test_admin_trigger_edit_view_updates_configuration(self):
        """Super Admin can configure delivery mode, subject override, CC/BCC, and threshold settings."""
        self.client.force_login(self.superadmin)
        rule = NotificationRule.objects.get(event_key="billing.subscription_expiry_user")

        post_data = {
            "delivery_mode": "BOTH",
            "subject_override": "Custom Warning: Subscription Ending Soon!",
            "cc_recipients": "accounting@tradeconnect.internal",
            "bcc_recipients": "archive@tradeconnect.internal",
            "threshold_value": "100.00",
            "cooldown_hours": "12",
            "reminder_days_before": "7",
            "is_enabled": "on",
        }
        url = reverse("admin-panel-email-trigger-edit", kwargs={"pk": rule.pk})
        res = self.client.post(url, post_data)
        self.assertEqual(res.status_code, 302)

        rule.refresh_from_db()
        self.assertEqual(rule.delivery_mode, "BOTH")
        self.assertEqual(rule.subject_override, "Custom Warning: Subscription Ending Soon!")
        self.assertEqual(rule.cc_recipients, "accounting@tradeconnect.internal")
        self.assertEqual(rule.bcc_recipients, "archive@tradeconnect.internal")
        self.assertEqual(rule.reminder_days_before, 7)

    def test_admin_trigger_preview_and_test_send_api(self):
        """Super Admin preview and test-send APIs return rendered HTML and dispatch test email."""
        self.client.force_login(self.superadmin)
        rule = NotificationRule.objects.get(event_key="admin.new_company")

        # 1. Preview API
        preview_url = reverse("admin-panel-email-trigger-preview", kwargs={"pk": rule.pk})
        p_res = self.client.get(preview_url)
        self.assertEqual(p_res.status_code, 200)
        p_data = p_res.json()
        self.assertEqual(p_data["status"], "success")
        self.assertIn("TradeConnect", p_data["html"])

        # 2. Test Send API (mocked SMTP)
        with patch("apps.notifications.services.email_engine.get_active_email_connection") as mock_conn:
            mock_backend = MagicMock()
            mock_backend.send_messages.return_value = 1
            mock_conn.return_value = (mock_backend, "noreply@tradeconnect.com")
            with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1) as mock_send:
                test_send_url = reverse("admin-panel-email-trigger-test-send", kwargs={"pk": rule.pk})
                ts_res = self.client.post(
                    test_send_url,
                    {"target_email": "tester@domain.com"},
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )
                self.assertEqual(ts_res.status_code, 200)
                ts_data = ts_res.json()
                self.assertEqual(ts_data["status"], "success")
                self.assertIn("tester@domain.com", ts_data["message"])


class InAppNotificationCenterTests(TestCase):
    """
    Automated test suite for the In-App System Notification Center:
    - In-app notification creation on business event triggers
    - Multi-channel routing (IN_APP, EMAIL, BOTH)
    - DRF APIs (list, unread-count, mark-read, mark-unread, mark-all-read, archive/delete)
    - Web inbox view, tabs, and filters
    - Company & user isolation security
    """

    def setUp(self):
        from apps.dashboard.models import Notification

        self.user_a = User.objects.create_user(
            email="usera@companyalpha.com",
            password="password123",
            first_name="User",
            last_name="Alpha",
        )
        self.company_a = Company.objects.create(
            name="Alpha Corp",
            company_type=Company.CompanyType.BUYER,
            email="ops@companyalpha.com",
            created_by=self.user_a,
        )
        CompanyMember.objects.create(
            company=self.company_a,
            user=self.user_a,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.user_b = User.objects.create_user(
            email="userb@companybeta.com",
            password="password123",
            first_name="User",
            last_name="Beta",
        )
        self.company_b = Company.objects.create(
            name="Beta Corp",
            company_type=Company.CompanyType.SELLER,
            email="sales@companybeta.com",
            created_by=self.user_b,
        )
        CompanyMember.objects.create(
            company=self.company_b,
            user=self.user_b,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Create template & rules for tests
        self.template = NotificationTemplate.objects.create(
            name="Inquiry Alert",
            event_key="seller.inquiry_received",
            category=NotificationTemplate.Category.SELLER,
            subject="New Inquiry from {{ company_name }}",
            html_content="<p>A new buyer inquiry was received.</p>",
            text_content="A new buyer inquiry was received.",
            is_active=True,
        )

        self.rule_both = NotificationRule.objects.create(
            name="Inquiry Alert Both",
            event_key="seller.inquiry_received",
            template=self.template,
            delivery_mode=NotificationRule.DeliveryMode.AUTOMATIC,
            channel=NotificationRule.Channel.BOTH,
            target_audience=NotificationRule.TargetAudience.USER,
            recipient_type=NotificationRule.RecipientType.ACTING_USER,
            severity=NotificationRule.Severity.WARNING,
            priority=NotificationRule.Priority.HIGH,
            is_enabled=True,
        )

        self.client_a = Client()
        self.client_a.force_login(self.user_a)

        self.client_b = Client()
        self.client_b.force_login(self.user_b)

    def test_in_app_notification_creation_on_trigger(self):
        """Triggering notification with channel=BOTH creates both Notification and NotificationLog."""
        from apps.dashboard.models import Notification

        Notification.objects.filter(user=self.user_a).delete()
        NotificationLog.objects.filter(user=self.user_a).delete()

        with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1):
            with patch("apps.notifications.services.email_engine.deliver_notification_log", return_value=True):
                logs = trigger_email_notification(
                    event_key="seller.inquiry_received",
                    company=self.company_a,
                    user=self.user_a,
                    context={"summary": "Buyer requested bulk quote for 500 units.", "cta_url": "/inquiries/123/"},
                    async_dispatch=False,
                )

        # Verify email log created
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].recipient_email, self.user_a.email)

        # Verify in-app notification created
        notifs = Notification.objects.filter(user=self.user_a)
        self.assertEqual(notifs.count(), 1)
        notif = notifs.first()
        self.assertEqual(notif.event_key, "seller.inquiry_received")
        self.assertEqual(notif.priority, Notification.Priority.WARNING)
        self.assertIn("Buyer requested bulk quote", notif.message)
        self.assertEqual(notif.link, "/inquiries/123/")
        self.assertFalse(notif.is_read)
        self.assertFalse(notif.is_archived)

    def test_channel_routing_in_app_only(self):
        """Rule with channel=IN_APP creates in-app Notification but skips NotificationLog & email."""
        from apps.dashboard.models import Notification

        self.rule_both.channel = NotificationRule.Channel.IN_APP
        self.rule_both.save()

        Notification.objects.filter(user=self.user_a).delete()
        NotificationLog.objects.filter(user=self.user_a).delete()

        logs = trigger_email_notification(
            event_key="seller.inquiry_received",
            company=self.company_a,
            user=self.user_a,
            context={"summary": "In-app only alert test."},
            async_dispatch=False,
        )

        self.assertEqual(len(logs), 0)
        self.assertEqual(NotificationLog.objects.filter(user=self.user_a).count(), 0)

        notifs = Notification.objects.filter(user=self.user_a)
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(notifs.first().message, "In-app only alert test.")

    def test_channel_routing_email_only(self):
        """Rule with channel=EMAIL creates NotificationLog but skips in-app Notification."""
        from apps.dashboard.models import Notification

        self.rule_both.channel = NotificationRule.Channel.EMAIL
        self.rule_both.save()

        Notification.objects.filter(user=self.user_a).delete()
        NotificationLog.objects.filter(user=self.user_a).delete()

        with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1):
            with patch("apps.notifications.services.email_engine.deliver_notification_log", return_value=True):
                logs = trigger_email_notification(
                    event_key="seller.inquiry_received",
                    company=self.company_a,
                    user=self.user_a,
                    context={"summary": "Email only alert test."},
                    async_dispatch=False,
                )

        self.assertEqual(len(logs), 1)
        self.assertEqual(Notification.objects.filter(user=self.user_a).count(), 0)

    def test_drf_api_list_and_unread_count(self):
        """DRF APIs return serialized notifications, unread counts, and priority filters."""
        from apps.dashboard.models import Notification

        Notification.objects.filter(user=self.user_a).delete()

        Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Normal Info Alert",
            message="Testing info alert",
            priority=Notification.Priority.INFO,
            is_read=False,
        )
        Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Urgent Security Alert",
            message="Testing urgent alert",
            priority=Notification.Priority.CRITICAL,
            is_read=False,
        )
        Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Read Alert",
            message="Already read",
            priority=Notification.Priority.SUCCESS,
            is_read=True,
        )

        # 1. Unread count API
        count_url = reverse("api-notifications-unread-count")
        res = self.client_a.get(count_url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["unread_count"], 2)
        self.assertEqual(data["critical_count"], 1)

        # 2. List API - All
        list_url = reverse("api-notifications-list")
        res = self.client_a.get(list_url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_count"], 3)
        self.assertEqual(data["unread_count"], 2)

        # 3. List API - Unread filter
        res = self.client_a.get(list_url + "?status=unread")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["total_count"], 2)

        # 4. List API - Priority filter
        res = self.client_a.get(list_url + "?priority=CRITICAL")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["total_count"], 1)
        self.assertEqual(res.json()["results"][0]["priority"], "CRITICAL")

    def test_drf_api_mark_read_and_archive(self):
        """DRF APIs allow marking as read, unread, marking all read, and archiving."""
        from apps.dashboard.models import Notification

        notif1 = Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Alert 1",
            message="Message 1",
            is_read=False,
        )
        notif2 = Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Alert 2",
            message="Message 2",
            is_read=False,
        )

        # Mark read single
        mark_read_url = reverse("api-notification-mark-read", kwargs={"pk": notif1.pk})
        res = self.client_a.post(mark_read_url)
        self.assertEqual(res.status_code, 200)
        notif1.refresh_from_db()
        self.assertTrue(notif1.is_read)
        self.assertEqual(res.json()["unread_count"], 1)

        # Mark unread single
        mark_unread_url = reverse("api-notification-mark-unread", kwargs={"pk": notif1.pk})
        res = self.client_a.post(mark_unread_url)
        self.assertEqual(res.status_code, 200)
        notif1.refresh_from_db()
        self.assertFalse(notif1.is_read)

        # Mark all read
        mark_all_url = reverse("api-notifications-mark-all-read")
        res = self.client_a.post(mark_all_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["unread_count"], 0)
        notif1.refresh_from_db()
        notif2.refresh_from_db()
        self.assertTrue(notif1.is_read)
        self.assertTrue(notif2.is_read)

        # Archive
        archive_url = reverse("api-notification-archive", kwargs={"pk": notif1.pk})
        res = self.client_a.post(archive_url)
        self.assertEqual(res.status_code, 200)
        notif1.refresh_from_db()
        self.assertTrue(notif1.is_archived)

    def test_user_and_company_isolation_security(self):
        """User B cannot access, read, or archive User A's notifications."""
        from apps.dashboard.models import Notification

        notif_a = Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Private Alert for Alpha",
            message="Alpha confidential message",
            is_read=False,
        )

        # User B attempts to mark User A's notification as read
        cross_read_url = reverse("api-notification-mark-read", kwargs={"pk": notif_a.pk})
        res = self.client_b.post(cross_read_url)
        self.assertEqual(res.status_code, 404)

        # User B attempts to archive User A's notification
        cross_archive_url = reverse("api-notification-archive", kwargs={"pk": notif_a.pk})
        res = self.client_b.post(cross_archive_url)
        self.assertEqual(res.status_code, 404)

        # User B's list does not contain User A's notification
        list_url = reverse("api-notifications-list")
        res = self.client_b.get(list_url)
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.json()["results"]]
        self.assertNotIn(notif_a.id, ids)

    def test_web_inbox_view_and_actions(self):
        """Web inbox displays tabs and handles toggle-read, archive, and restore."""
        from apps.dashboard.models import Notification

        notif = Notification.objects.create(
            user=self.user_a,
            company=self.company_a,
            title="Inbox Test Alert",
            message="Testing web inbox view",
            priority=Notification.Priority.WARNING,
            is_read=False,
        )

        inbox_url = reverse("notifications-inbox")
        res = self.client_a.get(inbox_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Inbox Test Alert")
        self.assertContains(res, "WARNING")

        # Toggle read
        toggle_url = reverse("notification-toggle-read", kwargs={"pk": notif.pk})
        res = self.client_a.get(toggle_url)
        self.assertEqual(res.status_code, 302)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

        # Archive
        archive_url = reverse("notification-archive", kwargs={"pk": notif.pk})
        res = self.client_a.get(archive_url)
        self.assertEqual(res.status_code, 302)
        notif.refresh_from_db()
        self.assertTrue(notif.is_archived)

        # Restore
        restore_url = reverse("notification-restore", kwargs={"pk": notif.pk})
        res = self.client_a.get(restore_url)
        self.assertEqual(res.status_code, 302)
        notif.refresh_from_db()
        self.assertFalse(notif.is_archived)


class ComprehensiveNotificationSystemAuditTests(TestCase):
    """
    Complete end-to-end audit test suite for the In-App + Email Notification Center across:
    1. All 3 user authorization tiers: User/Company User, Company Admin, Super Admin
    2. All 3 company types: Sales/Buyer, Vendor/Seller, Both
    3. Multi-tenant company data isolation and cross-company attack prevention
    4. Duplicate prevention (blocking exact spam while permitting distinct business items)
    5. Clean relative links (protecting against ERR_CONNECTION_REFUSED on hardcoded hosts)
    6. Notification outbox persistence, delivery attempts, and retry engine
    7. Mandatory security preference protections
    """

    def setUp(self):
        # 1. Super Admin
        self.superadmin = User.objects.create_superuser(
            email="sysadmin@platform.com",
            password="AdminPassword123!",
            first_name="Global",
            last_name="SuperAdmin",
        )
        self.client_super = Client()
        self.client_super.force_login(self.superadmin)

        # 2. Sales / Buyer Company & Users
        self.sales_admin = User.objects.create_user(
            email="alice.salesadmin@salesco.com",
            password="Password123!",
            first_name="Alice",
            last_name="SalesAdmin",
        )
        self.sales_user = User.objects.create_user(
            email="bob.buyeruser@salesco.com",
            password="Password123!",
            first_name="Bob",
            last_name="SalesStaff",
        )
        self.sales_company = Company.objects.create(
            name="Alpha Sales Corp",
            company_type=Company.CompanyType.BUYER,
            email="contact@salesco.com",
            created_by=self.sales_admin,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.sales_company,
            user=self.sales_admin,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.sales_company,
            user=self.sales_user,
            role=CompanyMember.Role.USER,
            is_active=True,
        )
        self.client_sales_admin = Client()
        self.client_sales_admin.force_login(self.sales_admin)
        self.client_sales_user = Client()
        self.client_sales_user.force_login(self.sales_user)

        # 3. Vendor / Seller Company & Users
        self.vendor_admin = User.objects.create_user(
            email="charlie.vendoradmin@vendorco.com",
            password="Password123!",
            first_name="Charlie",
            last_name="VendorAdmin",
        )
        self.vendor_company = Company.objects.create(
            name="Beta Vendor Ltd",
            company_type=Company.CompanyType.SELLER,
            email="contact@vendorco.com",
            created_by=self.vendor_admin,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.vendor_company,
            user=self.vendor_admin,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        self.client_vendor_admin = Client()
        self.client_vendor_admin.force_login(self.vendor_admin)

        # 4. Both (Sales + Vendor) Company & Admin
        self.both_admin = User.objects.create_user(
            email="elena.bothadmin@omnitraders.com",
            password="Password123!",
            first_name="Elena",
            last_name="BothAdmin",
        )
        self.both_company = Company.objects.create(
            name="Omni Global Traders",
            company_type=Company.CompanyType.BOTH,
            email="contact@omnitraders.com",
            created_by=self.both_admin,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.both_company,
            user=self.both_admin,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        self.client_both_admin = Client()
        self.client_both_admin.force_login(self.both_admin)

        # Ensure active templates & rules for testing
        self._ensure_test_rules()

    def _ensure_test_rules(self):
        """Ensure standard templates and rules exist for test execution."""
        from apps.notifications.management.commands.seed_email_templates import Command
        Command().handle()

    def test_user_tier_notifications(self):
        """Verify normal company user receives member welcome and search completed notifications."""
        from apps.dashboard.models import Notification

        # Trigger member welcome using standard auth.welcome event key
        res = trigger_email_notification(
            event_key="auth.welcome",
            company=self.sales_company,
            user=self.sales_user,
            context={
                "user_name": "Bob SalesStaff",
                "company_name": "Alpha Sales Corp",
                "recipient_email": "bob.buyeruser@salesco.com",
                "login_url": "/login/",
                "cta_url": "/dashboard/",
                "cta_text": "Access Workspace",
            },
        )
        self.assertTrue(len(res) > 0 or Notification.objects.filter(user=self.sales_user).exists())

        # Check in-app notification created for sales_user
        notifs = Notification.objects.filter(user=self.sales_user)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("Welcome", notifs.first().title)
        self.assertEqual(notifs.first().link, "/dashboard/")

    def test_company_admin_tier_notifications_and_isolation(self):
        """Verify Company Admin receives credit/billing and member alerts, and they are isolated to their company."""
        from apps.dashboard.models import Notification

        # Low credits triggered for Alpha Sales Corp
        res = trigger_email_notification(
            event_key="billing.credits_low",
            company=self.sales_company,
            context={
                "current_balance": 15,
                "threshold": 20,
                "cta_url": "/wallet/topup/",
                "cta_text": "Recharge Credits",
            },
        )
        self.assertTrue(len(res) > 0 or Notification.objects.filter(user=self.sales_admin).exists())

        # Sales admin should receive in-app notification
        sales_admin_notifs = Notification.objects.filter(user=self.sales_admin)
        self.assertGreaterEqual(sales_admin_notifs.count(), 1)
        self.assertTrue(any("credit" in n.title.lower() for n in sales_admin_notifs))

        # Vendor admin (Beta Vendor Ltd) MUST NOT receive this notification
        vendor_admin_notifs = Notification.objects.filter(user=self.vendor_admin)
        self.assertEqual(vendor_admin_notifs.count(), 0)

        # Both company admin MUST NOT receive this notification
        both_admin_notifs = Notification.objects.filter(user=self.both_admin)
        self.assertEqual(both_admin_notifs.count(), 0)

    def test_super_admin_tier_governance_alerts(self):
        """Verify Super Admin receives governance and new registration alerts."""
        from apps.dashboard.models import Notification

        # 1. Super Admin registration alert
        res_super = trigger_email_notification(
            event_key="admin.new_company",
            company=self.vendor_company,
            context={
                "company_name": self.vendor_company.name,
                "company_type": "Vendor",
                "admin_name": "Charlie VendorAdmin",
                "admin_email": "charlie@vendorco.com",
                "location": "Mumbai",
                "registered_at": "Today",
                "cta_url": f"/admin-panel/companies/{self.vendor_company.id}/",
            },
        )
        self.assertTrue(len(res_super) > 0 or Notification.objects.filter(user=self.superadmin).exists())
        super_notifs = Notification.objects.filter(user=self.superadmin)
        self.assertGreaterEqual(super_notifs.count(), 1)
        self.assertTrue(any("company" in n.title.lower() for n in super_notifs))

        # 2. Company suspension notice dispatched to affected company admin
        res_susp = trigger_email_notification(
            event_key="company.suspended",
            company=self.vendor_company,
            context={
                "company_name": self.vendor_company.name,
                "reason": "Policy compliance audit",
                "cta_url": f"/admin-panel/companies/{self.vendor_company.id}/",
                "cta_text": "Review Company",
            },
        )
        self.assertTrue(len(res_susp) > 0 or Notification.objects.filter(user=self.vendor_admin).exists())
        vendor_notifs = Notification.objects.filter(user=self.vendor_admin)
        self.assertGreaterEqual(vendor_notifs.count(), 1)
        self.assertTrue(any("suspend" in n.title.lower() for n in vendor_notifs))

    def test_duplicate_prevention_logic(self):
        """Verify duplicate events with identical titles are blocked within 15m, but distinct business items are allowed."""
        from apps.dashboard.models import Notification

        initial_count = Notification.objects.filter(user=self.sales_user).count()

        # Item 1
        trigger_email_notification(
            event_key="buyer.search_completed",
            company=self.sales_company,
            user=self.sales_user,
            context={
                "item_title": "Titanium Bolts",
                "results_count": 5,
                "cta_url": "/sourcing/results/101/",
                "cta_text": "View Results",
            },
        )
        self.assertEqual(Notification.objects.filter(user=self.sales_user).count(), initial_count + 1)

        # Identical Item 1 fired immediately again -> Duplicate check prevents spam
        trigger_email_notification(
            event_key="buyer.search_completed",
            company=self.sales_company,
            user=self.sales_user,
            context={
                "item_title": "Titanium Bolts",
                "results_count": 5,
                "cta_url": "/sourcing/results/101/",
                "cta_text": "View Results",
            },
        )
        self.assertEqual(Notification.objects.filter(user=self.sales_user).count(), initial_count + 1)

        # Item 2 (Different item under same event_key) -> MUST be allowed
        trigger_email_notification(
            event_key="buyer.search_completed",
            company=self.sales_company,
            user=self.sales_user,
            context={
                "item_title": "Carbon Fiber Sheets",
                "results_count": 12,
                "cta_url": "/sourcing/results/102/",
                "cta_text": "View Results",
            },
        )
        self.assertEqual(Notification.objects.filter(user=self.sales_user).count(), initial_count + 2)

    def test_navbar_bell_api_and_relative_links(self):
        """Verify navbar bell API returns unread count, list, and relative links (avoiding ERR_CONNECTION_REFUSED)."""
        from apps.dashboard.models import Notification

        # Create notification with absolute localhost url
        notif = Notification.objects.create(
            user=self.sales_admin,
            company=self.sales_company,
            title="Sourcing Update",
            message="Your quote has been reviewed.",
            link="http://127.0.0.1:8000/sourcing/inquiries/42/",
            priority=Notification.Priority.INFO,
            is_read=False,
        )

        # Call unread count API
        count_url = reverse("api-notifications-unread-count")
        res = self.client_sales_admin.get(count_url)
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.json().get("unread_count", 0), 1)

        # Call notifications list API
        list_url = reverse("api-notifications-list")
        res = self.client_sales_admin.get(list_url)
        self.assertEqual(res.status_code, 200)
        results = res.json().get("results", [])
        target = next((item for item in results if item["id"] == notif.id), None)
        self.assertIsNotNone(target)
        # Must be converted to relative link
        self.assertEqual(target["link"], "/sourcing/inquiries/42/")

        # Mark read via API
        mark_read_url = reverse("api-notification-mark-read", kwargs={"pk": notif.pk})
        res = self.client_sales_admin.post(mark_read_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("link"), "/sourcing/inquiries/42/")
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_cross_company_and_cross_user_security_isolation(self):
        """Verify strict authorization: users cannot read, mark read, or archive other users' notifications."""
        from apps.dashboard.models import Notification

        vendor_notif = Notification.objects.create(
            user=self.vendor_admin,
            company=self.vendor_company,
            title="Confidential Vendor Alert",
            message="Sensitive commercial information",
            priority=Notification.Priority.CRITICAL,
            is_read=False,
        )

        # Sales admin tries to access vendor admin's notification
        mark_url = reverse("api-notification-mark-read", kwargs={"pk": vendor_notif.pk})
        res = self.client_sales_admin.post(mark_url)
        self.assertEqual(res.status_code, 404)

        archive_url = reverse("api-notification-archive", kwargs={"pk": vendor_notif.pk})
        res = self.client_sales_admin.post(archive_url)
        self.assertEqual(res.status_code, 404)

        # Ensure vendor_notif was not affected
        vendor_notif.refresh_from_db()
        self.assertFalse(vendor_notif.is_read)
        self.assertFalse(vendor_notif.is_archived)

    def test_outbox_retry_and_attempt_persistence(self):
        """Verify NotificationLog records delivery failures and retry attempts in database."""
        outbox_log = NotificationLog.objects.create(
            event_key="auth.security_alert",
            recipient_email="test.retry@example.com",
            recipient_name="Retry Test",
            user=self.sales_admin,
            company=self.sales_company,
            subject="Security Alert",
            text_body="Suspicious login detected",
            html_body="<p>Suspicious login detected</p>",
            status=NotificationLog.Status.QUEUED,
            max_attempts=1,
        )

        with patch("apps.notifications.services.email_engine.get_active_email_connection") as mock_conn:
            mock_conn.side_effect = Exception("SMTP Gateway Connection Refused")
            res = deliver_notification_log(outbox_log)
            self.assertFalse(res)

        outbox_log.refresh_from_db()
        self.assertEqual(outbox_log.status, NotificationLog.Status.FAILED)
        self.assertEqual(outbox_log.attempts_count, 1)
        self.assertIn("Connection Refused", outbox_log.last_error)

        # Check EmailDeliveryAttempt was persisted
        attempts = EmailDeliveryAttempt.objects.filter(log=outbox_log)
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.first().status, EmailDeliveryAttempt.Status.FAILED)
        self.assertIn("Connection Refused", attempts.first().error_message)

        # Retry with success
        with patch("apps.notifications.services.email_engine.get_active_email_connection") as mock_conn:
            mock_instance = MagicMock()
            mock_conn.return_value = (mock_instance, False)
            mock_instance.send_messages.return_value = 1
            retry_res = retry_failed_notification_log(outbox_log)
            self.assertTrue(retry_res)

        outbox_log.refresh_from_db()
        self.assertEqual(outbox_log.status, NotificationLog.Status.SENT)
        self.assertIsNotNone(outbox_log.sent_at)
        self.assertEqual(EmailDeliveryAttempt.objects.filter(log=outbox_log).count(), 2)


class FirebaseWebPushNotificationTests(TestCase):
    """
    Comprehensive test suite for Firebase Cloud Messaging (FCM) Web Push:
    1. Device token registration & unregistration REST APIs.
    2. Public push-config endpoint safety (no private keys leaked).
    3. Multicast push dispatch and stale/unregistered token pruning.
    4. Multi-tenant company isolation: suspended company users receive no push alerts.
    5. User preference enforcement: push_enabled respected per category; mandatory security protected.
    6. Unified channel routing: ALL channel sends In-App, Email, and Push simultaneously.
    7. Root Service Worker endpoint serving valid JS and Service-Worker-Allowed header.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="pushuser@enterprise.com",
            password="Password123!",
            first_name="Push",
            last_name="Tester",
        )
        self.company = Company.objects.create(
            name="Push Co",
            company_type=Company.CompanyType.BUYER,
            email="contact@pushco.com",
            created_by=self.user,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.company,
            user=self.user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_device_token_registration_and_unregistration_api(self):
        """Verify device registration and unregistration REST APIs."""
        from apps.notifications.models import DeviceToken

        reg_url = reverse("api-notifications-device-register")
        res = self.client.post(
            reg_url,
            data=json.dumps({
                "token": "fcm_token_alpha_123",
                "device_type": "CHROME",
                "device_name": "Chrome on Windows",
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

        # Verify database record
        token_obj = DeviceToken.objects.filter(token="fcm_token_alpha_123").first()
        self.assertIsNotNone(token_obj)
        self.assertEqual(token_obj.user, self.user)
        self.assertEqual(token_obj.company, self.company)
        self.assertTrue(token_obj.is_active)

        # Unregister
        unreg_url = reverse("api-notifications-device-unregister")
        res_unreg = self.client.post(
            unreg_url,
            data=json.dumps({"token": "fcm_token_alpha_123"}),
            content_type="application/json",
        )
        self.assertEqual(res_unreg.status_code, 200)
        self.assertTrue(res_unreg.json().get("success"))

        token_obj.refresh_from_db()
        self.assertFalse(token_obj.is_active)

    def test_push_config_api_endpoint(self):
        """Verify push-config returns public settings without leaking private credentials."""
        url = reverse("api-notifications-push-config")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("enabled", data)
        self.assertIn("apiKey", data)
        self.assertIn("projectId", data)
        self.assertIn("vapidPublicKey", data)
        # Ensure private key is never exposed
        self.assertNotIn("private_key", data)
        self.assertNotIn("client_email", data)

    def test_fcm_multicast_dispatch_and_stale_token_cleanup(self):
        """Verify send_user_push sends messages and prunes unregistered tokens."""
        from apps.notifications.models import DeviceToken
        from apps.notifications.services.fcm_service import send_user_push

        t1 = DeviceToken.objects.create(
            user=self.user,
            company=self.company,
            token="valid_token_xyz",
            is_active=True,
        )
        t2 = DeviceToken.objects.create(
            user=self.user,
            company=self.company,
            token="stale_token_stale",
            is_active=True,
        )

        with patch("apps.notifications.services.fcm_service.get_firebase_app") as mock_app:
            mock_app.return_value = MagicMock()
            with patch("firebase_admin.messaging.send_each_for_multicast") as mock_send:
                from firebase_admin import messaging

                # Mock response: 1 success, 1 UnregisteredError
                resp_success = MagicMock()
                resp_success.success = True
                resp_fail = MagicMock()
                resp_fail.success = False
                resp_fail.exception = messaging.UnregisteredError("Requested entity was not found.")

                batch_resp = MagicMock()
                batch_resp.success_count = 1
                batch_resp.failure_count = 1
                batch_resp.responses = [resp_success, resp_fail]
                mock_send.return_value = batch_resp

                from apps.notifications.services.fcm_service import send_fcm_multicast
                res = send_fcm_multicast(
                    tokens=["valid_token_xyz", "stale_token_stale"],
                    title="Procurement Match Found",
                    body="AI matched 5 new suppliers for your requirement",
                    link="/sourcing/results/10/",
                )
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("sent_count"), 1)
                self.assertEqual(res.get("failed_count"), 1)

        # Verify stale token t2 was pruned (is_active=False)
        t2.refresh_from_db()
        self.assertFalse(t2.is_active)
        # Verify valid token t1 remains active
        t1.refresh_from_db()
        self.assertTrue(t1.is_active)

    def test_push_tenant_isolation_on_company_suspension(self):
        """Verify push alerts are blocked if company is suspended by platform governance."""
        from apps.notifications.models import DeviceToken
        from apps.notifications.services.fcm_service import send_user_push

        DeviceToken.objects.create(
            user=self.user,
            company=self.company,
            token="active_token_1",
            is_active=True,
        )

        # Suspend company
        self.company.is_active = False
        self.company.save(update_fields=["is_active"])

        with patch("apps.notifications.services.fcm_service.send_fcm_multicast") as mock_multi:
            res = send_user_push(
                user_or_users=self.user,
                title="Suspension Alert",
                body="Testing governance push suppression",
            )
            # send_fcm_multicast should not even be called
            mock_multi.assert_not_called()
            self.assertEqual(res.get("sent_count", 0), 0)

    def test_push_notification_preferences_respect(self):
        """Verify user's push_enabled preference is respected for non-mandatory categories."""
        from apps.notifications.models import DeviceToken, NotificationPreference
        from apps.notifications.services.fcm_service import send_user_push

        DeviceToken.objects.create(
            user=self.user,
            company=self.company,
            token="token_pref_test",
            is_active=True,
        )

        # Disable push for PROCUREMENT category
        NotificationPreference.objects.create(
            user=self.user,
            company=self.company,
            category=NotificationPreference.Category.PROCUREMENT,
            push_enabled=False,
        )

        with patch("apps.notifications.services.fcm_service.send_fcm_multicast") as mock_multi:
            res = send_user_push(
                user_or_users=self.user,
                title="RFQ Proposal Update",
                body="New proposal submitted",
                category=NotificationPreference.Category.PROCUREMENT,
            )
            mock_multi.assert_not_called()
            self.assertEqual(res.get("sent_count", 0), 0)

        # Security category must always be permitted regardless of preference
        with patch("apps.notifications.services.fcm_service.send_fcm_multicast") as mock_multi:
            mock_multi.return_value = {"success": True, "sent_count": 1}
            res_sec = send_user_push(
                user_or_users=self.user,
                title="Security Alert",
                body="Password change initiated",
                category=NotificationPreference.Category.SECURITY,
            )
            mock_multi.assert_called_once()

    def test_service_worker_endpoint_served_at_root(self):
        """Verify /firebase-messaging-sw.js is served with Service-Worker-Allowed header."""
        sw_url = reverse("firebase-service-worker")
        res = self.client.get(sw_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/javascript")
        self.assertEqual(res["Service-Worker-Allowed"], "/")
        self.assertIn(b"firebase.initializeApp", res.content)
        self.assertIn(b"onBackgroundMessage", res.content)


class NotificationModuleEnhancementAuditTests(TestCase):
    """
    Automated verification of enhanced subsystem capabilities:
    1. Strict email preference suppression for opted-out categories.
    2. Stalled outbox log self-healing in process_email_outbox.
    3. Super Admin template syntax validation pre-save error handling.
    4. Multi-select inbox bulk operations (mark read, unread, archive) with tenant isolation.
    """
    def setUp(self):
        self.user_1 = User.objects.create_user(
            email="enh_user1@apex.com",
            password="SecurePass@123",
            first_name="Ananya",
            last_name="Deshmukh",
        )
        self.user_2 = User.objects.create_user(
            email="enh_user2@competitor.com",
            password="SecurePass@123",
            first_name="Vikram",
            last_name="Singh",
        )
        self.company_1 = Company.objects.create(
            name="Apex Dynamics Enh",
            company_type=Company.CompanyType.SALES,
            created_by=self.user_1,
            is_active=True,
        )
        self.company_2 = Company.objects.create(
            name="Competitor Corp",
            company_type=Company.CompanyType.VENDOR,
            created_by=self.user_2,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.company_1,
            user=self.user_1,
            role="ADMIN",
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.company_2,
            user=self.user_2,
            role="ADMIN",
            is_active=True,
        )

        self.superuser = User.objects.create_superuser(
            email="admin_enh@platform.com",
            password="AdminPass@123",
            first_name="Admin",
            last_name="Super",
        )

    def test_email_preference_suppression_for_non_mandatory_category(self):
        """Verify email outbox creation is suppressed if user disabled email preference for category."""
        from apps.notifications.models import NotificationPreference, NotificationTemplate, NotificationRule, NotificationLog
        from apps.notifications.services.email_engine import trigger_email_notification

        # Create rule for SALES category
        tpl, _ = NotificationTemplate.objects.get_or_create(
            event_key="sales.test_lead_pref",
            defaults={
                "name": "Test Sales Lead Template",
                "category": NotificationTemplate.Category.SALES,
                "subject": "New Lead: {{ product_name }}",
                "html_content": "<p>A new lead has been identified.</p>",
                "is_active": True,
            }
        )
        NotificationRule.objects.get_or_create(
            event_key="sales.test_lead_pref",
            defaults={
                "name": "Test Sales Rule",
                "template": tpl,
                "recipient_type": NotificationRule.RecipientType.ACTING_USER,
                "channel": NotificationRule.Channel.EMAIL,
                "is_enabled": True,
            }
        )

        # Disable email for SALES category
        NotificationPreference.objects.create(
            user=self.user_1,
            company=self.company_1,
            category=NotificationPreference.Category.SALES,
            email_enabled=False,
        )

        with patch("apps.notifications.services.email_engine.deliver_notification_log") as mock_deliver:
            logs = trigger_email_notification(
                event_key="sales.test_lead_pref",
                user=self.user_1,
                company=self.company_1,
                context={"product_name": "Solar Inverter"},
                async_dispatch=False,
            )
            # Expect 0 logs created because email preference is disabled
            self.assertEqual(len(logs), 0)
            mock_deliver.assert_not_called()

        # Mandatory SECURITY category must always be permitted
        tpl_sec, _ = NotificationTemplate.objects.get_or_create(
            event_key="auth.test_security_pref",
            defaults={
                "name": "Security Notice",
                "category": NotificationTemplate.Category.AUTH,
                "subject": "Security Alert",
                "html_content": "<p>Your security settings have changed.</p>",
                "is_active": True,
            }
        )
        NotificationRule.objects.get_or_create(
            event_key="auth.test_security_pref",
            defaults={
                "name": "Security Notice Rule",
                "template": tpl_sec,
                "recipient_type": NotificationRule.RecipientType.ACTING_USER,
                "channel": NotificationRule.Channel.EMAIL,
                "is_enabled": True,
            }
        )
        # Attempt to set email_enabled=False for SECURITY
        NotificationPreference.objects.create(
            user=self.user_1,
            company=self.company_1,
            category=NotificationPreference.Category.SECURITY,
            email_enabled=False,
        )
        with patch("apps.notifications.services.email_engine.deliver_notification_log") as mock_deliver:
            mock_deliver.return_value = True
            sec_logs = trigger_email_notification(
                event_key="auth.test_security_pref",
                user=self.user_1,
                company=self.company_1,
                async_dispatch=False,
            )
            self.assertEqual(len(sec_logs), 1)

    def test_process_email_outbox_recovers_stalled_sending_logs(self):
        """Verify process_email_outbox command reclaims logs stranded in SENDING state > 10m."""
        from django.core.management import call_command
        from apps.notifications.models import NotificationLog

        tpl = NotificationTemplate.objects.filter(is_active=True).first()
        log = NotificationLog.objects.create(
            event_key="test.stalled_log",
            template=tpl,
            company=self.company_1,
            user=self.user_1,
            recipient_email="stalled@apex.com",
            subject="Stalled Transmission Test",
            html_body="<p>Test</p>",
            text_body="Test",
            status=NotificationLog.Status.SENDING,
            attempts_count=0,
            max_attempts=3,
        )
        # Manually set updated_at to 15 minutes ago
        past_time = timezone.now() - timezone.timedelta(minutes=15)
        NotificationLog.objects.filter(id=log.id).update(updated_at=past_time)

        # Run outbox with dry_run=False, mocking SMTP delivery
        with patch("apps.notifications.services.smtp_service.get_active_email_connection") as mock_conn:
            mock_smtp = MagicMock()
            mock_smtp.send_messages.return_value = 1
            mock_conn.return_value = (mock_smtp, None)

            call_command("process_email_outbox", batch_size=10, dry_run=False)

        log.refresh_from_db()
        # Should have been recovered and delivered (SENT)
        self.assertEqual(log.status, NotificationLog.Status.SENT)

    def test_admin_template_syntax_validation(self):
        """Verify pre-save Django Template syntax validation prevents invalid template syntax."""
        tpl = NotificationTemplate.objects.create(
            name="Syntax Test Template",
            event_key="test.syntax_check",
            category=NotificationTemplate.Category.SYSTEM,
            subject="Valid Subject {{ company_name }}",
            html_content="<p>Hello {{ recipient_name }}</p>",
            is_active=True,
            version=1,
        )

        self.client.force_login(self.superuser)

        # POST invalid template syntax (unclosed if tag)
        invalid_html = "<p>{% if user_status %}Welcome{% endif"
        edit_url = reverse("admin-panel-email-template-edit", kwargs={"pk": tpl.id})
        res_fail = self.client.post(edit_url, {
            "subject": "Syntax Error Test",
            "html_content": invalid_html,
            "text_content": "Text",
        })
        self.assertEqual(res_fail.status_code, 200)
        tpl.refresh_from_db()
        # Version and content must remain unchanged
        self.assertEqual(tpl.version, 1)
        self.assertNotEqual(tpl.html_content, invalid_html)

        # POST valid template syntax
        valid_html = "<p>{% if user_status %}Welcome{% endif %}</p>"
        res_ok = self.client.post(edit_url, {
            "subject": "Valid Subject",
            "html_content": valid_html,
            "text_content": "Valid Text",
        })
        self.assertEqual(res_ok.status_code, 302)
        tpl.refresh_from_db()
        self.assertEqual(tpl.version, 2)
        self.assertEqual(tpl.html_content, valid_html)

    def test_notifications_bulk_action_view(self):
        """Verify bulk action endpoint updates user notifications with strict tenant/user boundary isolation."""
        from apps.dashboard.models import Notification

        # Create 3 notifications for user_1
        n1 = Notification.objects.create(
            user=self.user_1, company=self.company_1, title="Alert 1", message="Msg 1", is_read=False
        )
        n2 = Notification.objects.create(
            user=self.user_1, company=self.company_1, title="Alert 2", message="Msg 2", is_read=False
        )
        n3 = Notification.objects.create(
            user=self.user_1, company=self.company_1, title="Alert 3", message="Msg 3", is_read=False
        )
        # Create 1 notification for user_2
        n_other = Notification.objects.create(
            user=self.user_2, company=self.company_2, title="Other Alert", message="Other Msg", is_read=False
        )

        self.client.force_login(self.user_1)
        bulk_url = reverse("notifications-bulk-action")

        # 1. Bulk Mark Read (including attempt on n_other.id)
        res_read = self.client.post(bulk_url, {
            "action": "mark_read",
            "selected_ids": [n1.id, n2.id, n_other.id],
        })
        self.assertEqual(res_read.status_code, 302)

        n1.refresh_from_db()
        n2.refresh_from_db()
        n3.refresh_from_db()
        n_other.refresh_from_db()

        self.assertTrue(n1.is_read)
        self.assertTrue(n2.is_read)
        self.assertFalse(n3.is_read)
        # Isolation: user_2's notification must NOT be modified by user_1!
        self.assertFalse(n_other.is_read)

        # 2. Bulk Archive
        res_arch = self.client.post(bulk_url, {
            "action": "archive",
            "selected_ids": [n1.id, n3.id],
        })
        self.assertEqual(res_arch.status_code, 302)

        n1.refresh_from_db()
        n3.refresh_from_db()
        self.assertTrue(n1.is_archived)
        self.assertTrue(n3.is_archived)


class DynamicNotificationAndPushSystemTests(TestCase):
    """
    Automated test suite for Dynamic In-App and Firebase Browser Push Notifications:
    1. Dynamic event dispatch for Customer, Vendor, and Customer + Vendor companies.
    2. Strict multi-tenant company isolation and RBAC recipient resolution.
    3. Independent In-App and Push user preferences.
    4. Deduplication key idempotency.
    5. PushDeliveryLog audit recording.
    6. Direct login page redirection upon logout for all users.
    """
    def setUp(self):
        self.client = Client()

        # Customer User & Company
        self.customer_user = User.objects.create_user(
            email="customer_lead@acme.com",
            password="SecurePass@123",
            first_name="Pooja",
            last_name="Sharma",
        )
        self.customer_company = Company.objects.create(
            name="Acme Customer Corp",
            company_type=Company.CompanyType.SALES,
            created_by=self.customer_user,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.customer_company,
            user=self.customer_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Vendor User & Company
        self.vendor_user = User.objects.create_user(
            email="vendor_sales@supremegoods.com",
            password="SecurePass@123",
            first_name="Ramesh",
            last_name="Gupta",
        )
        self.vendor_company = Company.objects.create(
            name="Supreme Goods Vendor",
            company_type=Company.CompanyType.VENDOR,
            created_by=self.vendor_user,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.vendor_company,
            user=self.vendor_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Both (Customer + Vendor) Company & User
        self.both_user = User.objects.create_user(
            email="hybrid_ops@globalb2b.com",
            password="SecurePass@123",
            first_name="Karan",
            last_name="Mehta",
        )
        self.both_company = Company.objects.create(
            name="Global Trade Hub",
            company_type=Company.CompanyType.BOTH,
            created_by=self.both_user,
            is_active=True,
        )
        CompanyMember.objects.create(
            company=self.both_company,
            user=self.both_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Super Admin
        self.super_admin = User.objects.create_superuser(
            email="platform_super@b2b.com",
            password="SecurePass@123",
            first_name="Super",
            last_name="Admin",
        )

    def test_dynamic_requirement_created_dispatches_to_vendors_only(self):
        """Verify requirement.created only notifies vendors and excludes non-vendor companies."""
        from apps.notifications.services.dynamic_engine import dispatch_dynamic_notification
        from apps.dashboard.models import Notification

        initial_vendor_notifs = Notification.objects.filter(user=self.vendor_user).count()
        initial_customer_notifs = Notification.objects.filter(user=self.customer_user).count()

        result = dispatch_dynamic_notification(
            event_key="requirement.created",
            context={
                "id": 999,
                "item_name": "Stainless Steel Fasteners",
                "company_name": self.customer_company.name,
                "quantity": "5000",
                "unit": "kg",
                "target_vendor_companies": [self.vendor_company],
            },
            company=self.customer_company,
            actor=self.customer_user,
        )
        self.assertEqual(result["in_app_created"], 1)

        # Vendor received notification
        self.assertEqual(Notification.objects.filter(user=self.vendor_user).count(), initial_vendor_notifs + 1)
        notif = Notification.objects.filter(user=self.vendor_user).latest("created_at")
        self.assertIn("Stainless Steel Fasteners", notif.title)
        self.assertIn("Customer Acme Customer Corp", notif.message)
        self.assertEqual(notif.link, "/sourcing/requirements/999/")

        # Customer actor did not receive vendor announcement
        self.assertEqual(Notification.objects.filter(user=self.customer_user).count(), initial_customer_customer := initial_customer_notifs)

    def test_deduplication_key_suppression(self):
        """Verify deduplication_key suppresses duplicate bursts for identical business events."""
        from apps.notifications.services.dynamic_engine import dispatch_dynamic_notification
        from apps.dashboard.models import Notification

        dedup_key = "unique_dedup_test_key_443"
        # First dispatch
        res1 = dispatch_dynamic_notification(
            event_key="credits.low",
            context={"balance": "₹45.00"},
            company=self.customer_company,
            deduplication_key=dedup_key,
        )
        self.assertEqual(res1["in_app_created"], 1)

        # Immediate second dispatch with same key must be suppressed
        res2 = dispatch_dynamic_notification(
            event_key="credits.low",
            context={"balance": "₹45.00"},
            company=self.customer_company,
            deduplication_key=dedup_key,
        )
        self.assertEqual(res2["in_app_created"], 0)

    @patch("apps.notifications.services.fcm_service.send_user_push")
    def test_independent_preferences_in_app_and_push(self, mock_push):
        """Verify In-App and Push preferences function independently without altering email."""
        from apps.notifications.services.dynamic_engine import dispatch_dynamic_notification
        from apps.dashboard.models import Notification
        from apps.notifications.models import NotificationPreference, DeviceToken

        mock_push.return_value = {"success": True, "sent_count": 1, "failed_count": 0, "errors": []}
        DeviceToken.objects.create(
            user=self.vendor_user,
            company=self.vendor_company,
            token="fcm_token_vendor_device_test_123",
            device_type="CHROME",
            is_active=True,
        )

        # 1. Turn OFF In-App, Keep Push ON
        NotificationPreference.objects.update_or_create(
            user=self.vendor_user,
            company=self.vendor_company,
            category=NotificationPreference.Category.SALES,
            defaults={"in_app_enabled": False, "push_enabled": True, "email_enabled": True},
        )

        Notification.objects.filter(user=self.vendor_user).delete()
        res_push_only = dispatch_dynamic_notification(
            event_key="inquiry.created",
            context={
                "id": 105,
                "subject": "Bulk order of Copper Rods",
                "customer_name": "Acme Customer Corp",
                "item_name": "Copper Rods",
                "target_company": self.vendor_company,
            },
            company=self.customer_company,
            actor=self.customer_user,
            deduplication_key="inquiry:105:push_only",
        )
        # In-App record skipped
        self.assertEqual(Notification.objects.filter(user=self.vendor_user).count(), 0)
        # Push dispatched
        self.assertEqual(res_push_only["push_dispatched"], 1)

        # 2. Turn ON In-App, Turn OFF Push
        NotificationPreference.objects.update_or_create(
            user=self.vendor_user,
            company=self.vendor_company,
            category=NotificationPreference.Category.SALES,
            defaults={"in_app_enabled": True, "push_enabled": False, "email_enabled": True},
        )
        mock_push.reset_mock()

        res_in_app_only = dispatch_dynamic_notification(
            event_key="inquiry.created",
            context={
                "id": 106,
                "subject": "Bulk order of Aluminum Ingots",
                "customer_name": "Acme Customer Corp",
                "item_name": "Aluminum Ingots",
                "target_company": self.vendor_company,
            },
            company=self.customer_company,
            actor=self.customer_user,
            deduplication_key="inquiry:106:in_app_only",
        )
        # In-App created
        self.assertEqual(Notification.objects.filter(user=self.vendor_user).count(), 1)
        # Push skipped
        self.assertEqual(res_in_app_only["push_dispatched"], 0)
        mock_push.assert_not_called()

    @patch("apps.notifications.services.fcm_service.send_user_push")
    def test_push_delivery_log_recorded_on_dispatch(self, mock_push):
        """Verify PushDeliveryLog telemetry is saved with accurate status and timestamps."""
        from apps.notifications.services.dynamic_engine import dispatch_dynamic_notification
        from apps.notifications.models import DeviceToken, PushDeliveryLog

        mock_push.return_value = {"success": True, "sent_count": 1, "failed_count": 0, "errors": []}
        device = DeviceToken.objects.create(
            user=self.vendor_user,
            company=self.vendor_company,
            token="fcm_token_telemetry_device_456",
            device_type="EDGE",
            is_active=True,
        )

        dispatch_dynamic_notification(
            event_key="inquiry.created",
            context={
                "id": 201,
                "subject": "Telemetry Inquiry",
                "customer_name": "Customer Co",
                "item_name": "Valves",
                "target_company": self.vendor_company,
            },
            company=self.customer_company,
            actor=self.customer_user,
            deduplication_key="inquiry:201:telemetry",
        )

        log = PushDeliveryLog.objects.filter(user=self.vendor_user, device=device).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, PushDeliveryLog.Status.SUCCESS)
        self.assertEqual(log.event_key, "inquiry.created")
        self.assertIsNotNone(log.sent_at)

    def test_logout_redirects_directly_to_login(self):
        """Verify company users, company admins, and super admins redirect directly to /login/ on logout."""
        # 1. Company User / Admin logout
        self.client.force_login(self.customer_user)
        response = self.client.get(reverse("logout-web"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

        # 2. Super Admin logout
        self.client.force_login(self.super_admin)
        response_sa = self.client.get(reverse("logout-web"))
        self.assertRedirects(response_sa, reverse("login"), fetch_redirect_response=False)





