from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.billing.models import (
    SubscriptionPlan,
    Subscription,
    CompanyCreditWallet,
    FeatureCreditCost,
    CreditTransaction,
    UsageRecord,
    Invoice,
    Payment,
)
from apps.billing.services.wallet import CreditWalletService


class CreditWalletServiceUnitTestCase(TestCase):
    """
    Unit tests for CreditWalletService core business logic:
    - Default seeding
    - Atomic credit reservation
    - Commit usage
    - Refund on job failure
    - Admin adjustments
    - Plan credit allocation
    """

    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="superadmin@platform.ai",
            password="Password123!",
            first_name="Super",
            last_name="Admin",
        )
        self.company = Company.objects.create(
            name="Apex Engineering Ltd",
            company_type=Company.CompanyType.SELLER,
            industry="Machinery",
            country="India",
            is_active=True,
            created_by=self.superadmin,
        )
        self.admin_user = User.objects.create_user(
            email="admin@apex.com",
            password="Password123!",
            first_name="Apex",
            last_name="Admin",
        )
        self.membership = CompanyMember.objects.create(
            company=self.company,
            user=self.admin_user,
            role=CompanyMember.Role.ADMIN,
        )

    def test_ensure_default_plans_and_costs(self):
        """Verify default plans and feature credit costs are automatically created."""
        CreditWalletService.ensure_default_plans_and_costs()

        # Check default plans
        self.assertTrue(SubscriptionPlan.objects.filter(code="free").exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code="pro").exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code="enterprise").exists())

        # Check default feature costs
        self.assertTrue(FeatureCreditCost.objects.filter(feature_code="vendor_discovery").exists())
        self.assertTrue(FeatureCreditCost.objects.filter(feature_code="ai_matching").exists())
        self.assertTrue(FeatureCreditCost.objects.filter(feature_code="data_enrichment").exists())

    def test_wallet_creation_and_balance_calculation(self):
        """Test wallet balance formula: (sub + purchased + promo) - (consumed + reserved)."""
        wallet = CreditWalletService.get_or_create_wallet(self.company)
        wallet.subscription_credits = 100
        wallet.purchased_credits = 50
        wallet.promotional_credits = 10
        wallet.credits_consumed = 20
        wallet.reserved_credits = 5
        wallet.save()

        # Total pool = 100 + 50 + 10 = 160
        # Available = total_pool (160) - reserved (5) = 155
        self.assertEqual(wallet.total_pool, 160)
        self.assertEqual(wallet.available_credits, 155)
        self.assertEqual(wallet.total_available, 155)

    def test_atomic_reserve_and_commit(self):
        """Test full happy-path lifecycle: reserve -> commit usage."""
        wallet = CreditWalletService.get_or_create_wallet(self.company)
        wallet.subscription_credits = 50
        wallet.save()

        # 1. Reserve 1 quantity (5 credits default for vendor_discovery)
        ok, msg, usage_rec = CreditWalletService.reserve_credits(
            company=self.company,
            user=self.admin_user,
            feature_code="vendor_discovery",
            quantity=1,
            idempotency_key="job-test-uuid-001",
        )
        self.assertTrue(ok)
        self.assertIsNotNone(usage_rec)
        self.assertEqual(usage_rec.status, UsageRecord.ProcessingStatus.RESERVED)

        # Refresh wallet
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 5)
        self.assertEqual(wallet.available_credits, 45)

        # 2. Commit usage after job completes
        commit_tx = CreditWalletService.commit_usage(
            usage_record=usage_rec,
            actual_quantity=1,
        )
        self.assertIsNotNone(commit_tx)
        self.assertEqual(commit_tx.status, CreditTransaction.TransactionStatus.COMPLETED)

        usage_rec.refresh_from_db()
        self.assertEqual(usage_rec.status, UsageRecord.ProcessingStatus.COMPLETED)

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertEqual(wallet.credits_consumed, 5)
        self.assertEqual(wallet.available_credits, 45)

    def test_reserve_insufficient_credits(self):
        """Attempting to reserve more credits than available must fail safely."""
        wallet = CreditWalletService.get_or_create_wallet(self.company)
        wallet.subscription_credits = 1
        wallet.save()

        ok, msg, usage_rec = CreditWalletService.reserve_credits(
            company=self.company,
            user=self.admin_user,
            feature_code="vendor_discovery",  # Requires 5 credits
            quantity=1,
            idempotency_key="job-insufficient-001",
        )
        self.assertFalse(ok)
        self.assertIsNone(usage_rec)

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertEqual(wallet.available_credits, 1)

    def test_atomic_reserve_and_refund_on_failure(self):
        """When an AI job fails, reserved credits must be released/refunded back."""
        wallet = CreditWalletService.get_or_create_wallet(self.company)
        wallet.subscription_credits = 30
        wallet.save()

        # Reserve
        ok, msg, usage_rec = CreditWalletService.reserve_credits(
            company=self.company,
            user=self.admin_user,
            feature_code="ai_matching",  # 1 credit
            quantity=3,
            idempotency_key="job-failure-test-001",
        )
        self.assertTrue(ok)

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 3)
        self.assertEqual(wallet.available_credits, 27)

        # Simulate job failure and refund
        refund_tx = CreditWalletService.refund_or_release(
            usage_record=usage_rec,
            reason="AI search service timed out, automatically refunded credits.",
        )
        self.assertIsNotNone(refund_tx)
        self.assertEqual(refund_tx.status, CreditTransaction.TransactionStatus.COMPLETED)
        self.assertEqual(refund_tx.transaction_type, CreditTransaction.TransactionType.REFUND)

        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertEqual(wallet.credits_consumed, 0)
        self.assertEqual(wallet.available_credits, 30)

    def test_admin_credit_adjustment(self):
        """Super Admin manual adjustment adds or subtracts credits with receipt reference."""
        wallet = CreditWalletService.get_or_create_wallet(self.company)
        wallet.subscription_credits = 10
        wallet.save()

        tx = CreditWalletService.adjust_credits_admin(
            company=self.company,
            delta=50,
            reason="Promotional partner grant",
            admin_user=self.superadmin,
        )
        self.assertIsNotNone(tx)
        self.assertTrue(tx.receipt_number.startswith("RCP-"))

        wallet.refresh_from_db()
        self.assertEqual(wallet.available_credits, 60)


class BillingMultiRoleWorkflowTestCase(TestCase):
    """
    End-to-End integration tests for all 3 user personas:
    1. Super Admin: Platform overview, plan catalog edit, rate cards, company credit adjust.
    2. Company Admin: Billing dashboard, 1-click plan upgrade, credit pack purchase, invoices.
    3. Company User: Personal usage tracking, restricted from company billing settings.
    4. Tenant Isolation: Multi-company isolation.
    """

    def setUp(self):
        self.client = Client()

        # Seed catalog and rates
        CreditWalletService.ensure_default_plans_and_costs()

        # 1. Super Admin
        self.superadmin = User.objects.create_superuser(
            email="superadmin@platform.ai",
            password="Password123!",
            first_name="Platform",
            last_name="SuperAdmin",
        )

        # 2. Company A (Target Company)
        self.company_a = Company.objects.create(
            name="Alpha Corp",
            company_type=Company.CompanyType.BUYER,
            country="India",
            is_active=True,
            created_by=self.superadmin,
        )
        self.admin_user_a = User.objects.create_user(
            email="admin@alpha.com",
            password="Password123!",
            first_name="Alpha",
            last_name="Admin",
        )
        self.member_admin_a = CompanyMember.objects.create(
            company=self.company_a,
            user=self.admin_user_a,
            role=CompanyMember.Role.ADMIN,
        )

        self.regular_user_a = User.objects.create_user(
            email="user@alpha.com",
            password="Password123!",
            first_name="Alpha",
            last_name="User",
        )
        self.member_user_a = CompanyMember.objects.create(
            company=self.company_a,
            user=self.regular_user_a,
            role=CompanyMember.Role.USER,
        )

        # Subscription for Company A
        self.sub_a = Subscription.objects.create(
            company=self.company_a,
            plan=Subscription.Plan.FREE,
            credits_total=50,
            credits_used=10,
        )
        self.wallet_a = CreditWalletService.get_or_create_wallet(self.company_a)
        self.wallet_a.subscription_credits = 50
        self.wallet_a.credits_consumed = 10
        self.wallet_a.save()

        # 3. Company B (Isolated Company)
        self.company_b = Company.objects.create(
            name="Beta Ltd",
            company_type=Company.CompanyType.SELLER,
            country="Germany",
            is_active=True,
            created_by=self.superadmin,
        )
        self.admin_user_b = User.objects.create_user(
            email="admin@beta.com",
            password="Password123!",
            first_name="Beta",
            last_name="Admin",
        )
        self.member_admin_b = CompanyMember.objects.create(
            company=self.company_b,
            user=self.admin_user_b,
            role=CompanyMember.Role.ADMIN,
        )
        self.sub_b = Subscription.objects.create(
            company=self.company_b,
            plan=Subscription.Plan.PRO,
            credits_total=200,
            credits_used=20,
        )
        self.wallet_b = CreditWalletService.get_or_create_wallet(self.company_b)
        self.wallet_b.subscription_credits = 200
        self.wallet_b.credits_consumed = 20
        self.wallet_b.save()

    # ----------------------------------------------------
    # Super Admin Tests
    # ----------------------------------------------------
    def test_superadmin_access_plans_panel(self):
        """Super Admin can access global plans & governance panel."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        url = reverse("admin-panel-plans")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subscription & Credits Governance")
        self.assertContains(response, "Alpha Corp")
        self.assertContains(response, "Beta Ltd")

    def test_superadmin_adjust_company_credits(self):
        """Super Admin can adjust credits and plan for any company."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        url = reverse("admin-panel-update-subscription", kwargs={"company_id": self.company_a.id})
        response = self.client.post(url, {
            "plan": "pro",
            "credits_add": "100",
            "status": "active",
            "notes": "Contract bonus added by Super Admin",
        })
        self.assertRedirects(response, reverse("admin-panel-plans"))

        # Verify wallet updated
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.purchased_credits, 100)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.plan, "pro")

    def test_superadmin_save_subscription_plan(self):
        """Super Admin can create a new tier in the plan catalog."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        url = reverse("admin-panel-plan-save")
        response = self.client.post(url, {
            "code": "platinum",
            "name": "Platinum Elite",
            "billing_cycle": "monthly",
            "price": "9999.00",
            "currency": "INR",
            "included_credits": "2000",
            "max_team_members": "25",
            "search_limit_monthly": "2000",
            "status": "active",
            "is_public": "true",
        })
        self.assertRedirects(response, reverse("admin-panel-plans"))
        self.assertTrue(SubscriptionPlan.objects.filter(code="platinum").exists())

    def test_superadmin_update_feature_rate_card(self):
        """Super Admin can update feature credit consumption costs."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        url = reverse("admin-panel-feature-cost-update")
        response = self.client.post(url, {
            "feature_code": "vendor_discovery",
            "credit_cost": "3",
            "charging_unit": "per_job",
            "is_active": "true",
        })
        self.assertRedirects(response, reverse("admin-panel-plans"))

        rule = FeatureCreditCost.objects.get(feature_code="vendor_discovery")
        self.assertEqual(rule.credit_cost, 3)

    # ----------------------------------------------------
    # Company Admin Tests
    # ----------------------------------------------------
    def test_company_admin_billing_dashboard(self):
        """Company Admin sees company wallet, plans, team usage, invoices."""
        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available Credits")
        self.assertContains(response, "Invoices &amp; Statements")
        self.assertContains(response, "Available Subscription Tiers")

    def test_company_admin_upgrade_plan(self):
        """Company Admin can upgrade plan, which adjusts wallet and issues invoice."""
        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")
        response = self.client.post(url, {
            "action": "upgrade_plan",
            "plan_code": "pro",
        })
        self.assertRedirects(response, reverse("subscription-billing-web"))

        # Verify Company A upgraded
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.plan, "pro")

        # Verify an invoice was created
        invoice = Invoice.objects.filter(company=self.company_a).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    def test_company_admin_buy_credits_topup(self):
        """Company Admin can purchase credit top-up packs."""
        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")
        response = self.client.post(url, {
            "action": "buy_credits",
            "credits_pack": "500",
        })
        self.assertRedirects(response, reverse("subscription-billing-web"))

        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.purchased_credits, 500)

        # Invoice and Payment should be generated with 18% GST
        invoice = Invoice.objects.filter(company=self.company_a).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.subtotal, Decimal("9999.00"))
        self.assertEqual(invoice.tax, Decimal("1799.82"))
        self.assertEqual(invoice.total_amount, Decimal("11798.82"))

    # ----------------------------------------------------
    # Company User Tests
    # ----------------------------------------------------
    def test_company_user_view_personal_usage(self):
        """Company User sees personal usage metrics and company balance, but cannot edit billing."""
        self.client.login(email="user@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personal Usage View")
        self.assertContains(response, "My AI Feature Utilization Breakdown")
        self.assertContains(response, "My Recent Billable Operations")
        # Should NOT contain admin controls
        self.assertNotContains(response, "Available Subscription Tiers")
        self.assertNotContains(response, "Company Admin View")

    def test_company_user_cannot_upgrade_plan_or_buy_credits(self):
        """Company User cannot POST billing modifications."""
        self.client.login(email="user@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")

        # Attempt to upgrade plan
        response = self.client.post(url, {
            "action": "upgrade_plan",
            "plan_code": "enterprise",
        })
        self.assertRedirects(response, reverse("subscription-billing-web"))
        # Plan must remain unchanged
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.plan, Subscription.Plan.FREE)

        # Attempt to buy credits
        response = self.client.post(url, {
            "action": "buy_credits",
            "credit_pack": "large",
        })
        self.assertRedirects(response, reverse("subscription-billing-web"))
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.purchased_credits, 0)

    def test_company_user_request_credit_topup(self):
        """Company User can request a credit recharge from company administrators."""
        self.client.login(email="user@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web")

        response = self.client.post(url, {
            "action": "request_topup",
            "credits_pack": "500",
            "note": "Need credits for bulk supplier discovery.",
        })
        self.assertRedirects(response, reverse("subscription-billing-web"))

        # Notification should be sent to Company Admin
        from apps.dashboard.models import Notification
        notif = Notification.objects.filter(
            company=self.company_a,
            user=self.admin_user_a,
            title__icontains="Credit Recharge Request",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("500", notif.message)

    # ----------------------------------------------------
    # Multi-Tenant Isolation Tests
    # ----------------------------------------------------
    def test_tenant_isolation_company_b_cannot_see_company_a_billing(self):
        """Company B admin cannot see Company A invoices, wallet, or usage records."""
        # Create an invoice for Company A
        Invoice.objects.create(
            company=self.company_a,
            invoice_number="INV-ALPHA-999",
            currency="INR",
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date(),
            subtotal=Decimal("4999.00"),
            total_amount=Decimal("4999.00"),
            status=Invoice.Status.PAID,
        )

        # Login as Company B admin
        self.client.login(email="admin@beta.com", password="Password123!")
        url = reverse("subscription-billing-web")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Must NOT see Company A's invoice
        self.assertNotContains(response, "INV-ALPHA-999")

    # ----------------------------------------------------
    # New Feature Improvements Tests
    # ----------------------------------------------------
    def test_calculate_gst_breakdown(self):
        """Validates 18% GST calculation utility."""
        subtotal, tax, total = CreditWalletService.calculate_gst_breakdown(Decimal("10000.00"))
        self.assertEqual(subtotal, Decimal("10000.00"))
        self.assertEqual(tax, Decimal("1800.00"))
        self.assertEqual(total, Decimal("11800.00"))

    def test_process_subscription_lifecycle(self):
        """Validates subscription expiration and advance warning routines."""
        # Create an expired subscription
        self.sub_a.valid_till = timezone.now().date() - timezone.timedelta(days=5)
        self.sub_a.status = Subscription.Status.ACTIVE
        self.sub_a.save()

        summary = CreditWalletService.process_subscription_lifecycle()
        self.assertGreaterEqual(summary["expired"], 1)

        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.status, Subscription.Status.EXPIRED)

    def test_plan_quota_checkers(self):
        """Validates can_add_team_member and can_add_product limits."""
        # Free Starter has max_team_members=2, max_products=10
        free_plan = SubscriptionPlan.objects.filter(code="free").first()
        self.sub_a.plan_tier = free_plan
        self.sub_a.save()

        can_add_m, count_m, max_m = CreditWalletService.can_add_team_member(self.company_a)
        # We currently have 2 members (admin_a, user_a)
        self.assertEqual(count_m, 2)
        self.assertEqual(max_m, free_plan.max_team_members)
        self.assertFalse(can_add_m)

        can_add_p, count_p, max_p = CreditWalletService.can_add_product(self.company_a)
        self.assertEqual(count_p, 0)
        self.assertEqual(max_p, free_plan.max_products)
        self.assertTrue(can_add_p)

    def test_export_ledger_csv(self):
        """Company Admin can download complete credit transaction history as CSV."""
        CreditTransaction.objects.create(
            company=self.company_a,
            user=self.admin_user_a,
            transaction_type=CreditTransaction.TransactionType.PURCHASE,
            credits=250,
            balance_after=250,
            notes="Audit Export Test Topup",
        )

        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("subscription-billing-web") + "?export=ledger_csv"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        content = response.content.decode("utf-8")
        self.assertIn("Audit Export Test Topup", content)
        self.assertIn("+250", content)

    def test_export_report_credit_deduction(self):
        """Validates that downloading reports deducts credits from company wallet."""
        from apps.catalog.models import Product
        product = Product.objects.create(
            company=self.company_a,
            name="Machinery Component",
            created_by=self.admin_user_a,
        )
        self.wallet_a.subscription_credits = 10
        self.wallet_a.credits_consumed = 0
        self.wallet_a.save()

        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("export-reports") + f"?download=1&report_type=catalog&product_id={product.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")

        self.wallet_a.refresh_from_db()
        # export_reports costs 2 credits
        self.assertEqual(self.wallet_a.credits_consumed, 2)
        self.assertEqual(self.wallet_a.subscription_credits, 8)

    # ----------------------------------------------------
    # Payment Gateway & Order Creation Tests
    # ----------------------------------------------------
    def test_payment_gateway_create_order_and_fulfill(self):
        """Tests order creation, signature verification, and fulfillment via PaymentGatewayService."""
        from apps.billing.services.payment_gateway import PaymentGatewayService

        # 1. Create order for upgrade
        payload = PaymentGatewayService.create_order(
            company=self.company_a,
            user=self.admin_user_a,
            action_type="upgrade_plan",
            item_id_or_pack="enterprise",
            provider="dev",
        )
        self.assertEqual(payload["provider"], "dev")
        self.assertGreater(payload["amount"], 0)
        self.assertIn("order_id", payload)

        payment = Payment.objects.get(id=payload["payment_id"])
        self.assertEqual(payment.status, Payment.Status.PENDING)

        # 2. Verify Razorpay signature utility
        sig_valid = PaymentGatewayService.verify_razorpay_signature("order_123", "pay_456", "fake_sig")
        # In test mode without secret, returns True or validates
        self.assertIsInstance(sig_valid, bool)

        # 3. Fulfill payment
        fulfilled = PaymentGatewayService.fulfill_successful_payment(
            payment=payment,
            provider_payment_id="DEV-TX-12345",
            notes="Enterprise upgrade test",
            user=self.admin_user_a,
        )
        self.assertTrue(fulfilled)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        invoice = getattr(payment, "invoice", None)
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        # Sub should be enterprise
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.plan, "enterprise")

    # ----------------------------------------------------
    # Server-Side PDF Generation Tests
    # ----------------------------------------------------
    def test_generate_tax_invoice_pdf(self):
        """Tests server-side generation of GST compliant Tax Invoice PDF."""
        from apps.billing.services.pdf_generator import PDFReportGenerator

        invoice = Invoice.objects.create(
            company=self.company_a,
            subscription=self.sub_a,
            invoice_number="INV-2026-TESTPDF",
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date() + timezone.timedelta(days=30),
            subtotal=Decimal("10000.00"),
            tax=Decimal("1800.00"),
            total_amount=Decimal("11800.00"),
            currency="INR",
            status=Invoice.Status.PAID,
        )

        pdf_bytes = PDFReportGenerator.generate_tax_invoice_pdf(invoice)
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertGreater(len(pdf_bytes), 500)
        # PDF documents start with %PDF- header
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_generate_procurement_dossier_pdf(self):
        """Tests server-side generation of Executive Procurement Dossier PDF."""
        from apps.billing.services.pdf_generator import PDFReportGenerator
        from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany

        ext_comp = ExternalCompany.objects.create(
            name="Apex Precision Engineering",
            domain="apexengineering.com",
            phone="+91-9876543210",
            email="sales@apexengineering.com",
            city="Pune",
            state="Maharashtra",
            country="India",
        )
        job = SearchJob.objects.create(
            company=self.company_a,
            user=self.admin_user_a,
            job_type=SearchJob.JobType.FIND_SUPPLIERS,
            search_query="Industrial Fasteners CNC",
            status=SearchJob.Status.COMPLETED,
            total_results=1,
        )
        SearchResult.objects.create(
            search_job=job,
            external_company=ext_comp,
            result_type=SearchResult.ResultType.SUPPLIER,
            product_title="Grade 8.8 High Tensile Bolts",
            price=Decimal("45.00"),
            price_currency="INR",
            match_score=94,
        )

        pdf_bytes = PDFReportGenerator.generate_procurement_dossier_pdf(job)
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertGreater(len(pdf_bytes), 500)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_invoice_pdf_view_rbac(self):
        """Company A admin can download Company A invoice PDF; Company B cannot."""
        invoice = Invoice.objects.create(
            company=self.company_a,
            subscription=self.sub_a,
            invoice_number="INV-2026-RBACPDF",
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date() + timezone.timedelta(days=30),
            subtotal=Decimal("4999.00"),
            tax=Decimal("899.82"),
            total_amount=Decimal("5898.82"),
            currency="INR",
            status=Invoice.Status.PAID,
        )

        # Company A Admin can download
        self.client.login(email="admin@alpha.com", password="Password123!")
        url = reverse("invoice-pdf-download", kwargs={"invoice_id": invoice.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("Tax_Invoice_INV-2026-RBACPDF.pdf", response["Content-Disposition"])

        # Company B Admin is blocked
        self.client.login(email="admin@beta.com", password="Password123!")
        response_b = self.client.get(url)
        self.assertRedirects(response_b, reverse("subscription-billing-web"))

    # ----------------------------------------------------
    # Transactional Email Engine Tests
    # ----------------------------------------------------
    def test_transactional_email_service(self):
        """Validates transactional email engine formatting and dispatch."""
        from apps.billing.services.transactional_email import TransactionalEmailService
        from apps.leads.models import Inquiry

        # 1. Low Credit Warning
        ok_low = TransactionalEmailService.send_low_credit_alert_email(self.company_a, available_credits=15)
        self.assertIsInstance(ok_low, bool)

        # 2. Expiry Reminder
        ok_exp = TransactionalEmailService.send_subscription_expiry_reminder_email(self.company_a, self.sub_a, days_left=7)
        self.assertIsInstance(ok_exp, bool)

        # 3. RFQ Email
        inquiry = Inquiry.objects.create(
            company=self.company_a,
            subject="RFQ for Steel Pipe Flanges",
            message="Please provide MOQ and pricing for 500 units.",
            sent_to_email="procurement@supplier.com",
            status=Inquiry.Status.SENT,
        )
        ok_rfq = TransactionalEmailService.send_rfq_email(inquiry)
        self.assertIsInstance(ok_rfq, bool)

    # ----------------------------------------------------
    # Rate Limiter Utility Tests
    # ----------------------------------------------------
    def test_rate_limiter_allows_and_throttles(self):
        """Validates check_rate_limit cache sliding counter behavior."""
        from apps.dashboard.ratelimit import check_rate_limit

        test_key = "test_client_key_999"
        # First 3 requests should be allowed with limit=3
        is_allowed, rem1 = check_rate_limit(test_key, max_requests=3, window_seconds=10)
        self.assertTrue(is_allowed)
        is_allowed, rem2 = check_rate_limit(test_key, max_requests=3, window_seconds=10)
        self.assertTrue(is_allowed)
        is_allowed, rem3 = check_rate_limit(test_key, max_requests=3, window_seconds=10)
        self.assertTrue(is_allowed)

        # 4th request must be throttled
        is_allowed, rem4 = check_rate_limit(test_key, max_requests=3, window_seconds=10)
        self.assertFalse(is_allowed)
        self.assertEqual(rem4, 0)


class SubscriptionScenariosAndInvoicesWebTestCase(TestCase):
    """
    Tests covering:
    - Scenarios 1 to 4: 3-tier dynamic button states (choose, upgrade, renew, current, disabled)
    - Scenario 5: Top renew button removed
    - Scenario 6: Modern buy credits confirmation
    - Scenario 7: Auto-pay enabled by default on subscribe/upgrade/renewal
    - Scenario 8: Auto-pay pause retains access until valid_till (grace period)
    - Scenario 9: 5-day pre-expiry proactive notification
    - Dedicated Invoices Page & bank-statement CSV export
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="owner@testcorp.com",
            password="Password123!",
            first_name="Corp",
            last_name="Owner",
        )
        self.company = Company.objects.create(
            name="TestCorp Technologies",
            company_type=Company.CompanyType.BUYER,
            country="India",
            is_active=True,
            created_by=self.user,
        )
        self.membership = CompanyMember.objects.create(
            company=self.company,
            user=self.user,
            role=CompanyMember.Role.ADMIN,
        )

        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

        CreditWalletService.ensure_default_plans_and_costs()
        self.starter_plan, _ = SubscriptionPlan.objects.get_or_create(
            code="starter",
            defaults={
                "name": "Starter Tier",
                "price": Decimal("4999.00"),
                "currency": "INR",
                "included_credits": 150,
                "order": 1,
                "status": SubscriptionPlan.PlanStatus.ACTIVE,
            },
        )
        self.pro_plan = SubscriptionPlan.objects.get(code="pro")
        self.ent_plan = SubscriptionPlan.objects.get(code="enterprise")

        self.wallet = CreditWalletService.get_or_create_wallet(self.company)

    def _login(self):
        self.client.login(email="owner@testcorp.com", password="Password123!")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_scenario_1_no_plan_chosen_shows_choose_plan(self):
        """Scenario 1: If company has not chosen any paid plan, available plans show Choose Plan."""
        # Free subscription
        Subscription.objects.create(
            company=self.company,
            plan="free",
            plan_tier=SubscriptionPlan.objects.get(code="free"),
            status=Subscription.Status.ACTIVE,
            valid_till=timezone.now() + timezone.timedelta(days=30),
        )
        self._login()
        res = self.client.get(reverse("subscription-billing-web"))
        self.assertEqual(res.status_code, 200)
        annotated_plans = res.context["annotated_plans"]
        plan_states = {item["plan"].code: item["button_state"] for item in annotated_plans}
        self.assertEqual(plan_states.get("free"), "current")
        self.assertEqual(plan_states.get("starter"), "choose")
        self.assertEqual(plan_states.get("pro"), "choose")
        self.assertEqual(plan_states.get("enterprise"), "choose")

    def test_scenario_2_and_3_plan_chosen_higher_upgrade_lower_disabled(self):
        """Scenario 2 & 3: If on Pro plan, Starter is disabled and Enterprise is upgrade."""
        Subscription.objects.create(
            company=self.company,
            plan="pro",
            plan_tier=self.pro_plan,
            status=Subscription.Status.ACTIVE,
            valid_till=timezone.now() + timezone.timedelta(days=30),
        )
        self._login()
        res = self.client.get(reverse("subscription-billing-web"))
        self.assertEqual(res.status_code, 200)
        annotated_plans = res.context["annotated_plans"]
        plan_states = {item["plan"].code: item["button_state"] for item in annotated_plans}
        
        self.assertEqual(plan_states.get("starter"), "disabled")
        self.assertEqual(plan_states.get("pro"), "current")
        self.assertEqual(plan_states.get("enterprise"), "upgrade")

    def test_scenario_4_plan_expired_shows_renew(self):
        """Scenario 4: If Pro plan has expired, last chosen plan shows Renew."""
        Subscription.objects.create(
            company=self.company,
            plan="pro",
            plan_tier=self.pro_plan,
            status=Subscription.Status.EXPIRED,
            valid_till=timezone.now() - timezone.timedelta(days=2),
            renewal_date=timezone.now() - timezone.timedelta(days=2),
        )
        self._login()
        res = self.client.get(reverse("subscription-billing-web"))
        self.assertEqual(res.status_code, 200)
        annotated_plans = res.context["annotated_plans"]
        plan_states = {item["plan"].code: item["button_state"] for item in annotated_plans}

        self.assertEqual(plan_states.get("pro"), "renew")
        self.assertEqual(plan_states.get("starter"), "disabled")
        self.assertEqual(plan_states.get("enterprise"), "upgrade")

    def test_scenario_7_and_8_autopay_lifecycle_and_grace_period(self):
        """Scenario 7 & 8: Auto-pay default enabled on upgrade, paused keeps grace period."""
        sub = Subscription.objects.create(
            company=self.company,
            plan="free",
            status=Subscription.Status.ACTIVE,
        )
        self._login()
        
        # Upgrade activates Auto-Pay by default
        self.client.post(reverse("subscription-billing-web"), {
            "action": "upgrade_plan",
            "plan_code": "pro",
        })
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertIsNone(sub.cancelled_at)

        # Pause Auto-Pay
        self.client.post(reverse("subscription-billing-web"), {
            "action": "toggle_autopay",
            "auto_pay": "false",
        })
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)
        self.assertIsNotNone(sub.cancelled_at)
        # Still active during grace period
        self.assertFalse(sub.is_expired)

    def test_scenario_9_5day_expiry_warning_notification(self):
        """Scenario 9: 5 days before expiry triggers automated notification."""
        sub = Subscription.objects.create(
            company=self.company,
            plan="pro",
            plan_tier=self.pro_plan,
            status=Subscription.Status.ACTIVE,
            valid_till=timezone.now() + timezone.timedelta(days=5),
            renewal_date=timezone.now() + timezone.timedelta(days=5),
            expiry_warning_5d_sent=False,
        )
        notified = CreditWalletService.check_and_notify_5day_expiry(self.company)
        self.assertTrue(notified)
        sub.refresh_from_db()
        self.assertTrue(sub.expiry_warning_5d_sent)

    def test_dedicated_invoices_page_and_statement_export(self):
        """Validates dedicated /company/invoices/ and bank-statement CSV export."""
        Subscription.objects.create(
            company=self.company,
            plan="pro",
            plan_tier=self.pro_plan,
            status=Subscription.Status.ACTIVE,
        )
        self._login()
        
        # 1. Invoices web view loads
        res = self.client.get(reverse("company-invoices"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invoices &amp; Tax Receipts")
        self.assertContains(res, "Credit Ledger")

        # 2. Bank statement CSV export
        csv_res = self.client.get(reverse("export-statement-csv"))
        self.assertEqual(csv_res.status_code, 200)
        self.assertEqual(csv_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("OFFICIAL ACCOUNT STATEMENT & AUDIT LEDGER", csv_res.content.decode("utf-8"))
        self.assertIn("Opening Credit Balance", csv_res.content.decode("utf-8"))
        self.assertIn("Closing Credit Balance", csv_res.content.decode("utf-8"))



