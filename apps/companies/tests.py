from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.billing.models import Subscription, CreditTransaction
from apps.ai_search.models import APILog, SearchJob
from apps.dashboard.models import ActivityLog, PlatformSetting
from apps.catalog.models import Product
from apps.requirements.models import Requirement
from apps.leads.models import Inquiry


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
        """Super Admin can access audit trail; deprecated api-logs redirects to audit trail."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Deprecated API Logs route redirects to audit trail
        response = self.client.get(reverse("admin-panel-apilogs"))
        self.assertRedirects(response, reverse("admin-panel-auditlogs"))

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
        self.assertContains(response, "Platform Users")
        self.assertContains(response, "Plans &amp; Credits")
        self.assertContains(response, "Reports &amp; Exports")
        self.assertContains(response, "Audit Trail")
        self.assertNotIn("API Costs", response.content.decode("utf-8"))

        # Should NOT contain Buyer or Seller navigation sections
        content = response.content.decode("utf-8")
        self.assertNotIn('href="/procurement/requirements/"', content)
        self.assertNotIn('href="/procurement/find-suppliers/"', content)
        self.assertNotIn('href="/sales/products/"', content)
        self.assertNotIn('href="/sales/find-buyers/"', content)

        # Should NOT contain Django Root in Super Admin sidebar
        self.assertNotIn("Django Root", content)
        self.assertContains(response, 'href="/admin-panel/users/"')

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

    def test_login_page_superadmin_mode_and_no_self_registration(self):
        """Login page includes Super Admin mode, removes Create Account link, and routes Super Admin to admin-panel."""
        # 1. GET login page
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)

        # Super Admin tab present
        self.assertContains(res, "Super Admin")
        self.assertContains(res, "tabSuperAdminBtn")

        # 'Create Account (Sign Up)' link removed
        self.assertNotContains(res, "Create Account (Sign Up)")
        self.assertContains(res, "Enterprise accounts are onboarded exclusively by Platform Super Admins")

        # 2. POST login with Super Admin credentials
        post_res = self.client.post(reverse("login"), {
            "email": "superadmin@platform.ai",
            "password": "Password123!",
        }, follow=True)
        self.assertEqual(post_res.status_code, 200)

        # Should be redirected directly to admin-panel-dashboard
        self.assertContains(post_res, "Platform Executive Dashboard")
        self.assertContains(post_res, "Super Admin Dashboard")

    def test_admin_panel_company_delete_view(self):
        """Super Admin can soft-delete (archive) and restore a company with data preserved."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        test_comp = Company.objects.create(
            name="Temporary Test Corp",
            company_type=Company.CompanyType.BUYER,
            created_by=self.superadmin,
        )
        comp_id = test_comp.id
        self.assertTrue(Company.objects.filter(id=comp_id).exists())

        # 1. POST soft-delete
        res = self.client.post(reverse("admin-panel-company-delete", kwargs={"pk": comp_id}), follow=True)
        self.assertEqual(res.status_code, 200)

        # Record still exists in database, but marked as deleted
        test_comp.refresh_from_db()
        self.assertTrue(test_comp.is_deleted)
        self.assertFalse(test_comp.is_active)
        self.assertIsNotNone(test_comp.deleted_at)
        self.assertContains(res, "soft-deleted")

        # 2. Check that soft-deleted company does not appear in normal list, but appears in status=deleted
        list_res = self.client.get(reverse("admin-panel-companies"))
        self.assertNotContains(list_res, "Temporary Test Corp")

        archived_res = self.client.get(reverse("admin-panel-companies") + "?status=deleted")
        self.assertContains(archived_res, "Temporary Test Corp")

        # 3. POST restore
        restore_res = self.client.post(reverse("admin-panel-company-restore", kwargs={"pk": comp_id}), follow=True)
        self.assertEqual(restore_res.status_code, 200)
        test_comp.refresh_from_db()
        self.assertFalse(test_comp.is_deleted)
        self.assertTrue(test_comp.is_active)
        self.assertIsNone(test_comp.deleted_at)
        self.assertContains(restore_res, "successfully restored")

    def test_one_click_company_admin_password_reset(self):
        """Super Admin can reset any company user/admin password directly from dossier."""
        # Non-superadmin cannot reset password
        self.client.login(email="regular@vendor.com", password="Password123!")
        denied_res = self.client.post(
            reverse("admin-panel-reset-user-password", kwargs={"company_id": self.company.pk, "user_id": self.regular_user.pk}),
            {"new_password": "NewSecretPassword999!"},
        )
        self.assertEqual(denied_res.status_code, 403)

        # Super Admin resets password with custom password
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        reset_res = self.client.post(
            reverse("admin-panel-reset-user-password", kwargs={"company_id": self.company.pk, "user_id": self.regular_user.pk}),
            {"new_password": "NewSecretPassword999!"},
            follow=True,
        )
        self.assertEqual(reset_res.status_code, 200)

        # Target user can now authenticate with new password
        self.client.logout()
        login_success = self.client.login(email="regular@vendor.com", password="NewSecretPassword999!")
        self.assertTrue(login_success)

        # Super Admin auto-generates password (empty string) via JSON API
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        json_res = self.client.post(
            reverse("admin-panel-reset-user-password", kwargs={"company_id": self.company.pk, "user_id": self.regular_user.pk}),
            {"new_password": "", "format": "json"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(json_res.status_code, 200)
        data = json_res.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["auto_generated"])
        self.assertTrue(len(data["password"]) >= 10)

        # Authenticate with auto-generated password
        self.client.logout()
        login_auto = self.client.login(email="regular@vendor.com", password=data["password"])
        self.assertTrue(login_auto)

    def test_ai_credit_top_up_receipt_number(self):
        """Credit allocations auto-generate reference receipt numbers (e.g. RCP-12345) and display them in ledgers."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        res = self.client.post(
            reverse("admin-panel-update-subscription", kwargs={"company_id": self.company.pk}),
            data={
                "plan": "enterprise",
                "credits_add": "250",
                "notes": "Annual enterprise allocation",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        tx = CreditTransaction.objects.filter(company=self.company).first()
        self.assertIsNotNone(tx)
        self.assertIsNotNone(tx.receipt_number)
        self.assertTrue(tx.receipt_number.startswith("RCP-"))
        self.assertContains(res, tx.receipt_number)

    def test_platform_maintenance_mode_switch(self):
        """Super Admin can toggle maintenance mode and regular users see banner."""
        from apps.dashboard.models import PlatformSetting

        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # 1. Enable Maintenance Mode
        res = self.client.post(
            reverse("admin-panel-toggle-maintenance"),
            data={
                "action": "enable",
                "maintenance_message": "Scheduled database migration in progress. Expected downtime: 15 mins.",
            },
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(PlatformSetting.get_setting("maintenance_mode"), "true")
        self.assertContains(res, "Platform Maintenance Mode is now ENABLED")

        # 2. Regular user views page and sees maintenance banner
        self.client.login(email="regular@vendor.com", password="Password123!")
        user_res = self.client.get(reverse("dashboard"))
        self.assertEqual(user_res.status_code, 200)
        self.assertContains(user_res, "Platform Maintenance Mode Active")
        self.assertContains(user_res, "Scheduled database migration in progress")

        # 3. Super Admin disables maintenance mode
        self.client.login(email="superadmin@platform.ai", password="Password123!")
        disable_res = self.client.post(
            reverse("admin-panel-toggle-maintenance"),
            data={"action": "disable"},
            follow=True,
        )
        self.assertEqual(disable_res.status_code, 200)
        self.assertEqual(PlatformSetting.get_setting("maintenance_mode"), "false")
        self.assertContains(disable_res, "Platform Maintenance Mode has been DISABLED")

    def test_change_company_type_transitions(self):
        """Super Admin can transition company type between SELLER, BUYER, and BOTH."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Transition SELLER -> BUYER
        res = self.client.post(
            reverse("admin-panel-change-company-type", kwargs={"pk": self.company.pk}),
            {"company_type": "BUYER"},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.company_type, Company.CompanyType.BUYER)

        # Transition BUYER -> BOTH
        res = self.client.post(
            reverse("admin-panel-change-company-type", kwargs={"pk": self.company.pk}),
            {"company_type": "BOTH"},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.company_type, Company.CompanyType.BOTH)

    def test_admin_panel_platform_management_views(self):
        """Super Admin can access all platform management views and execute moderation actions."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # Seed data
        prod = Product.objects.create(
            company=self.company,
            name="CNC Milling Machine",
            sku="CNC-001",
            price=250000,
        )
        req = Requirement.objects.create(
            company=self.company,
            item_name="Industrial Steel Sheets",
            quantity=500,
            unit="sheets",
            created_by=self.superadmin,
        )
        job = SearchJob.objects.create(
            company=self.company,
            search_query="Industrial Steel",
            status=SearchJob.Status.COMPLETED,
            total_results=5,
        )
        inq = Inquiry.objects.create(
            company=self.company,
            subject="RFQ for Steel Sheets",
            sent_to_email="sales@tatasteel.com",
            message="Please provide quotation for 500 steel sheets.",
            status=Inquiry.Status.SENT,
        )

        # Roles view
        res = self.client.get(reverse("admin-panel-roles"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Roles & Responsibilities")

        # Products view & moderation toggle
        res = self.client.get(reverse("admin-panel-products"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "CNC Milling Machine")
        toggle_res = self.client.post(
            reverse("admin-panel-products"),
            {"action": "toggle_deleted", "product_id": prod.pk},
            follow=True,
        )
        self.assertEqual(toggle_res.status_code, 200)
        prod.refresh_from_db()
        self.assertTrue(prod.is_deleted)

        # Requirements view
        res = self.client.get(reverse("admin-panel-requirements"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Industrial Steel Sheets")

        # AI Providers view
        res = self.client.get(reverse("admin-panel-ai-providers"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Google Gemini")
        self.assertContains(res, "OpenAI")

        # Search Activity view
        res = self.client.get(reverse("admin-panel-search-activity"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Industrial Steel")

        # Inquiries view
        res = self.client.get(reverse("admin-panel-inquiries"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "RFQ for Steel Sheets")

        # Reports view & CSV exports
        res = self.client.get(reverse("admin-panel-reports"))
        self.assertEqual(res.status_code, 200)
        for export_type in ["companies_csv", "users_csv", "searches_csv", "inquiries_csv"]:
            csv_res = self.client.get(reverse("admin-panel-reports"), {"export": export_type})
            self.assertEqual(csv_res.status_code, 200)
            self.assertEqual(csv_res["Content-Type"], "text/csv; charset=utf-8")

        # Settings view GET and POST
        res = self.client.get(reverse("admin-panel-settings"))
        self.assertEqual(res.status_code, 200)
        post_settings = self.client.post(
            reverse("admin-panel-settings"),
            {
                "app_name": "Antigravity Procurement AI",
                "support_email": "ops@procurement.ai",
                "default_signup_credits": "100",
                "allow_public_signup": "true",
                "maintenance_mode": "false",
                "maintenance_message": "Upgrades scheduled",
            },
            follow=True,
        )
        self.assertEqual(post_settings.status_code, 200)
        self.assertEqual(PlatformSetting.get_setting("app_name"), "Antigravity Procurement AI")

    def test_direct_user_password_reset(self):
        """Super Admin can directly reset password of any user from users management view."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        res = self.client.post(
            reverse("admin-panel-user-reset-password-direct", kwargs={"pk": self.regular_user.pk}),
            {"new_password": "DirectNewPassword123!"},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        self.client.logout()
        auth_ok = self.client.login(email="regular@vendor.com", password="DirectNewPassword123!")
        self.assertTrue(auth_ok)

    def test_super_admin_dashboard_cache_and_range(self):
        """Super Admin dashboard supports dynamic date ranges and manual cache refresh (?refresh=1)."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        # 7d view (default)
        res_7d = self.client.get(reverse("admin-panel-dashboard") + "?range=7d")
        self.assertEqual(res_7d.status_code, 200)
        self.assertEqual(res_7d.context["date_range"], "7d")

        # 30d view
        res_30d = self.client.get(reverse("admin-panel-dashboard") + "?range=30d")
        self.assertEqual(res_30d.status_code, 200)
        self.assertEqual(res_30d.context["date_range"], "30d")

        # 90d view
        res_90d = self.client.get(reverse("admin-panel-dashboard") + "?range=90d")
        self.assertEqual(res_90d.status_code, 200)
        self.assertEqual(res_90d.context["date_range"], "90d")

        # YTD view
        res_ytd = self.client.get(reverse("admin-panel-dashboard") + "?range=ytd")
        self.assertEqual(res_ytd.status_code, 200)
        self.assertEqual(res_ytd.context["date_range"], "ytd")

        # Refresh cache
        res_refresh = self.client.get(reverse("admin-panel-dashboard") + "?range=7d&refresh=1")
        self.assertEqual(res_refresh.status_code, 200)

    def test_super_admin_bulk_actions_companies(self):
        """Super Admin can execute bulk operations on companies (verify, suspend, activate)."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        c2 = Company.objects.create(name="Batch Test Co", company_type=Company.CompanyType.BUYER, created_by=self.superadmin)
        ids = [str(self.company.id), str(c2.id)]

        # Bulk verify
        res = self.client.post(reverse("admin-panel-companies"), {
            "bulk_action": "verify",
            "selected_ids": ids,
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.company.refresh_from_db()
        c2.refresh_from_db()
        self.assertTrue(self.company.is_verified)
        self.assertTrue(c2.is_verified)

        # Bulk suspend
        res = self.client.post(reverse("admin-panel-companies"), {
            "bulk_action": "suspend",
            "selected_ids": ids,
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.company.refresh_from_db()
        c2.refresh_from_db()
        self.assertFalse(self.company.is_active)
        self.assertFalse(c2.is_active)

        # Bulk activate
        res = self.client.post(reverse("admin-panel-companies"), {
            "bulk_action": "activate",
            "selected_ids": ids,
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.company.refresh_from_db()
        c2.refresh_from_db()
        self.assertTrue(self.company.is_active)
        self.assertTrue(c2.is_active)

    def test_super_admin_bulk_actions_users(self):
        """Super Admin can batch deactivate users while protecting self from lockout."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        u2 = User.objects.create_user(email="testuser2@vendor.com", password="Password123!")
        # Attempt to batch-deactivate regular users and self
        ids = [str(self.regular_user.id), str(u2.id), str(self.superadmin.id)]

        res = self.client.post(reverse("admin-panel-users"), {
            "bulk_action": "deactivate",
            "selected_ids": ids,
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        self.regular_user.refresh_from_db()
        u2.refresh_from_db()
        self.superadmin.refresh_from_db()

        self.assertFalse(self.regular_user.is_active)
        self.assertFalse(u2.is_active)
        self.assertTrue(self.superadmin.is_active)  # self-lockout prevented

    def test_super_admin_requirements_moderation_toggle(self):
        """Super Admin can delist/restore buyer requirements."""
        self.client.login(email="superadmin@platform.ai", password="Password123!")

        req = Requirement.objects.create(
            company=self.company,
            item_name="Precision Ball Bearings",
            quantity=100,
            unit="pcs",
            created_by=self.superadmin,
        )
        self.assertFalse(req.is_deleted)

        # Toggle to deleted
        res = self.client.post(reverse("admin-panel-requirements"), {
            "action": "toggle_deleted",
            "requirement_id": req.id,
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertTrue(req.is_deleted)

        # Toggle back to active
        res2 = self.client.post(reverse("admin-panel-requirements"), {
            "action": "toggle_deleted",
            "requirement_id": req.id,
        }, follow=True)
        self.assertEqual(res2.status_code, 200)
        req.refresh_from_db()
        self.assertFalse(req.is_deleted)



