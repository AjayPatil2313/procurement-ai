from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.billing.models import Subscription, CreditTransaction
from apps.ai_search.models import APILog
from apps.dashboard.models import ActivityLog


class SuperAdminSuiteTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Create Super Admin
        self.superadmin = User.objects.create_superuser(
            email="superadmin@platform.ai",
            password="Password123!",
            first_name="Global",
            last_name="SuperAdmin",
        )

        # 2. Create Regular Company User
        self.regular_user = User.objects.create_user(
            email="regular@vendor.com",
            password="Password123!",
            first_name="Jane",
            last_name="Doe",
        )

        # 3. Create Target Company
        self.company = Company.objects.create(
            name="Apex Engineering Ltd",
            company_type=Company.CompanyType.SELLER,
            industry="Machinery",
            country="India",
            is_active=True,
            is_verified=False,
            created_by=self.superadmin,
        )

        # 4. Membership & Subscription
        self.membership = CompanyMember.objects.create(
            company=self.company,
            user=self.regular_user,
            role=CompanyMember.Role.ADMIN,
        )

        self.subscription = Subscription.objects.create(
            company=self.company,
            plan=Subscription.Plan.FREE,
            credits_total=50,
            credits_used=10,
        )

        # 5. Seed an APILog and ActivityLog
        APILog.objects.create(
            provider="Google Custom Search",
            endpoint="/customsearch/v1",
            status_code=200,
            cost=0.0050,
        )

        ActivityLog.objects.create(
            company=self.company,
            user=self.superadmin,
            activity_type=ActivityLog.ActivityType.MEMBER_INVITED,
            title="System Initialized",
            description="Super admin initialized system",
        )

    def test_super_admin_dashboard_security(self):
        """Regular users cannot view Super Admin dashboard (403), Super Admin gets 200 OK."""
        # Regular user denied
        self.client.login(email="regular@vendor.com", password="Password123!")
        response = self.client.get(reverse("admin-panel-dashboard"))
        self.assertEqual(response.status_code, 403)

        # Super Admin authorized
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        response = self.client.get(reverse("admin-panel-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform Executive Dashboard")
        self.assertIn("total_companies", response.context)
        self.assertEqual(response.context["total_companies"], 1)

    def test_toggle_company_status_and_verification(self):
        """Super Admin can activate/suspend and verify/unverify any company."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Toggle Active -> Suspended
        response = self.client.post(
            reverse("admin-panel-toggle-company-status", kwargs={"pk": self.company.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.company.refresh_from_db()
        self.assertFalse(self.company.is_active)

        # Toggle Suspended -> Active
        response = self.client.post(
            reverse("admin-panel-toggle-company-status", kwargs={"pk": self.company.pk}),
            follow=True,
        )
        self.company.refresh_from_db()
        self.assertTrue(self.company.is_active)

        # Toggle Verification -> Verified
        response = self.client.post(
            reverse("admin-panel-toggle-company-verification", kwargs={"pk": self.company.pk}),
            follow=True,
        )
        self.company.refresh_from_db()
        self.assertTrue(self.company.is_verified)

    def test_update_company_subscription_and_credits(self):
        """Super Admin can upgrade company plan and adjust credit pool."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Upgrade plan to PRO and add 100 credits
        response = self.client.post(
            reverse("admin-panel-update-subscription", kwargs={"company_id": self.company.pk}),
            data={
                "plan": "pro",
                "credits_add": "100",
                "notes": "Super admin enterprise onboarding promotion",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, "pro")
        self.assertEqual(self.subscription.credits_total, 150)  # 50 + 100

        # Verify CreditTransaction record
        tx = CreditTransaction.objects.filter(company=self.company).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.credits, 100)
        self.assertEqual(tx.transaction_type, CreditTransaction.TransactionType.CREDIT)

    def test_toggle_user_active_and_prevent_self_lockout(self):
        """Super Admin can deactivate normal users, but cannot lock out own account."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Deactivate regular user
        response = self.client.post(
            reverse("admin-panel-toggle-user-active", kwargs={"pk": self.regular_user.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.regular_user.refresh_from_db()
        self.assertFalse(self.regular_user.is_active)

        # Attempt to deactivate self
        response = self.client.post(
            reverse("admin-panel-toggle-user-active", kwargs={"pk": self.superadmin.pk}),
            follow=True,
        )
        self.superadmin.refresh_from_db()
        self.assertTrue(self.superadmin.is_active)
        self.assertContains(response, "cannot deactivate your own Super Admin account")

    def test_api_logs_and_audit_trail_views(self):
        """Super Admin can view API logs and system audit trail."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # API Logs View
        response = self.client.get(reverse("admin-panel-apilogs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google Custom Search")
        self.assertIn("monthly_budget", response.context)

        # Audit Logs View
        response = self.client.get(reverse("admin-panel-auditlogs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Initialized")

    def test_admin_panel_company_create_view(self):
        """Super Admin can onboard a new company with subscription credits and admin owner."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # GET form
        response = self.client.get(reverse("admin-panel-company-create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Onboard New Enterprise Account")

        # POST create company
        post_data = {
            "name": "Zenith Global Corp",
            "legal_name": "Zenith Global Corporation Pvt Ltd",
            "company_type": "BOTH",
            "industry": "Renewable Energy",
            "country": "India",
            "city": "Bengaluru",
            "state": "Karnataka",
            "website": "https://www.zenithglobal.com",
            "email": "info@zenithglobal.com",
            "phone": "+91 9123456789",
            "gst_vat_no": "29ABCDE1234F1Z5",
            "is_verified": "1",
            "is_active": "1",
            "plan": "pro",
            "credits_total": "300",
            "admin_email": "ceo@zenithglobal.com",
            "admin_first_name": "Vikram",
            "admin_last_name": "Singh",
            "admin_password": "SecurePassword123!",
        }
        response = self.client.post(reverse("admin-panel-company-create"), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Verify company created
        new_company = Company.objects.filter(name="Zenith Global Corp").first()
        self.assertIsNotNone(new_company)
        self.assertTrue(new_company.is_verified)
        self.assertEqual(new_company.industry, "Renewable Energy")

        # Verify subscription & credits
        sub = Subscription.objects.filter(company=new_company).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.plan, "pro")
        self.assertEqual(sub.credits_total, 300)

        # Verify initial company admin
        ceo_user = User.objects.filter(email="ceo@zenithglobal.com").first()
        self.assertIsNotNone(ceo_user)
        self.assertEqual(ceo_user.first_name, "Vikram")
        member = CompanyMember.objects.filter(company=new_company, user=ceo_user).first()
        self.assertIsNotNone(member)
        self.assertEqual(member.role, CompanyMember.Role.ADMIN)

    def test_super_admin_sidebar_exclusivity(self):
        """Super Admin sidebar contains ONLY Super Admin controls and excludes Buyer/Seller sections."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        response = self.client.get(reverse("admin-panel-dashboard"))
        self.assertEqual(response.status_code, 200)

        # Should contain Super Admin items
        self.assertContains(response, "Super Admin Dashboard")
        self.assertContains(response, "All Companies")
        self.assertContains(response, "Add New Company")
        self.assertContains(response, "Plans & Credits")
        self.assertContains(response, "API Costs")

        # Should NOT contain Buyer or Seller navigation sections
        content = response.content.decode("utf-8")
        self.assertNotIn('href="/procurement/requirements/"', content)
        self.assertNotIn('href="/procurement/find-suppliers/"', content)
        self.assertNotIn('href="/sales/products/"', content)
        self.assertNotIn('href="/sales/find-buyers/"', content)

        # Should NOT contain Django Root or standalone Users in Super Admin sidebar
        self.assertNotIn("Django Root", content)
        self.assertNotIn('href="/admin-panel/users/"', content)

    def test_admin_panel_company_detail_view(self):
        """Super Admin can access 360 company dossier showing members, products, plans, etc."""
        # Regular user denied (403)
        self.client.login(email="regular@vendor.com", password="Password123!")
        res = self.client.get(reverse("admin-panel-company-detail", kwargs={"pk": self.company.pk}))
        self.assertEqual(res.status_code, 403)

        # Super Admin authorized (200)
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        res = self.client.get(reverse("admin-panel-company-detail", kwargs={"pk": self.company.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Apex Engineering Ltd")
        self.assertContains(res, "regular@vendor.com")
        self.assertContains(res, "Company 360 Dossier")
        self.assertIn("members", res.context)
        self.assertIn("subscription", res.context)

    def test_admin_panel_company_edit_view(self):
        """Super Admin can edit company profile details and flags."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # GET form
        res = self.client.get(reverse("admin-panel-company-edit", kwargs={"pk": self.company.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Edit Corporate Account")

        # POST update
        update_data = {
            "name": "Apex Advanced Systems Ltd",
            "legal_name": "Apex Advanced Systems Private Limited",
            "company_type": "BOTH",
            "industry": "Robotics & Automation",
            "country": "India",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411001",
            "website": "https://apexsystems.example.com",
            "email": "contact@apexsystems.example.com",
            "phone": "+91 9988776655",
            "gst_vat_no": "27XYZAB1234C1D9",
            "is_verified": "1",
            "is_active": "1",
        }
        res = self.client.post(
            reverse("admin-panel-company-edit", kwargs={"pk": self.company.pk}),
            data=update_data,
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Apex Advanced Systems Ltd")
        self.assertEqual(self.company.industry, "Robotics & Automation")
        self.assertEqual(self.company.city, "Pune")
        self.assertTrue(self.company.is_verified)

    def test_companies_table_links_and_edit_action(self):
        """Companies table contains clickable dossier link and Edit Company action button instead of Enter."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        res = self.client.get(reverse("admin-panel-companies"))
        self.assertEqual(res.status_code, 200)

        # Check for link to Company 360 Dossier
        detail_url = reverse("admin-panel-company-detail", kwargs={"pk": self.company.pk})
        self.assertContains(res, detail_url)

        # Check for Edit Company button
        edit_url = reverse("admin-panel-company-edit", kwargs={"pk": self.company.pk})
        self.assertContains(res, edit_url)
        self.assertContains(res, "Edit Company")
