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
            self.assertIn("Buy &bull; Sell &bull; Grow", final_html)
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


