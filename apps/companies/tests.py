from django.test import TestCase, Client
from django.urls import reverse

from decimal import Decimal
from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.billing.models import Subscription, CreditTransaction, CompanyWallet
from apps.ai_search.models import APILog, SearchJob
from apps.dashboard.models import ActivityLog, PlatformSetting, BuyerModuleConfig, SellerModuleConfig, BothDashboardLayout
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
        self.assertContains(response, "Admin Dashboard")
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
        self.assertContains(response, "Admin Dashboard")
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
        self.assertContains(post_res, "Admin Dashboard")

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


class SuperAdminDashboardSyncTestCase(TestCase):
    """
    Validates end-to-end synchronization between Super Admin panel and
    Buyer, Seller, and Both (Hybrid) dashboards, including active backend
    permission enforcement, module matrix toggles, and workspace switching.
    """

    def setUp(self):
        BuyerModuleConfig.ensure_defaults()
        SellerModuleConfig.ensure_defaults()

        self.superadmin = User.objects.create_superuser(
            email="superadmin_sync@platform.ai",
            password="Password123!",
            first_name="Global",
            last_name="SuperAdmin",
        )

        # 1. Buyer Company & User
        self.buyer_comp = Company.objects.create(
            name="Buyer Corp India",
            company_type=Company.CompanyType.BUYER,
            is_active=True,
            created_by=self.superadmin,
        )
        self.buyer_user = User.objects.create_user(
            email="buyer_sync@buyer.com",
            password="Password123!",
            first_name="Raj",
            last_name="Kumar",
        )
        CompanyMember.objects.create(
            company=self.buyer_comp,
            user=self.buyer_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 2. Seller Company & User
        self.seller_comp = Company.objects.create(
            name="Seller Corp India",
            company_type=Company.CompanyType.SELLER,
            is_active=True,
            created_by=self.superadmin,
        )
        self.seller_user = User.objects.create_user(
            email="seller_sync@seller.com",
            password="Password123!",
            first_name="Amit",
            last_name="Shah",
        )
        CompanyMember.objects.create(
            company=self.seller_comp,
            user=self.seller_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 3. Both (Hybrid) Company & User
        self.both_comp = Company.objects.create(
            name="Hybrid Conglomerate",
            company_type=Company.CompanyType.BOTH,
            is_active=True,
            created_by=self.superadmin,
        )
        self.both_user = User.objects.create_user(
            email="hybrid_sync@hybrid.com",
            password="Password123!",
            first_name="Pooja",
            last_name="Verma",
        )
        CompanyMember.objects.create(
            company=self.both_comp,
            user=self.both_user,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.company = self.buyer_comp
        self.regular_user = self.buyer_user
        CompanyWallet.objects.get_or_create(company=self.buyer_comp, defaults={"available_balance": Decimal("5000.00")})

    def test_super_admin_governance_views_access_control(self):
        """Super Admin can access all dashboard governance views; regular users receive 403."""
        admin_urls = [
            reverse("admin-panel-buyer-dashboard"),
            reverse("admin-panel-seller-dashboard"),
            reverse("admin-panel-both-dashboard"),
            reverse("admin-panel-modules"),
            reverse("admin-panel-permissions"),
            reverse("admin-panel-transactions"),
            reverse("admin-panel-wallets"),
        ]

        # Regular user denied
        self.client.login(email="buyer_sync@buyer.com", password="Password123!")
        for url in admin_urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 403, f"Non-admin should be forbidden from {url}")

        # Super Admin granted
        self.client.login(email="superadmin_sync@platform.ai", password="Password123!")
        for url in admin_urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, f"Super Admin should have 200 OK on {url}")

    def test_buyer_module_disable_enforces_backend_route_403(self):
        """Disabling a Buyer module via Super Admin actively blocks backend route access with 403."""
        self.client.login(email="buyer_sync@buyer.com", password="Password123!")
        # Accessible initially
        res_initial = self.client.get(reverse("requirements-list"))
        self.assertEqual(res_initial.status_code, 200)

        # Super Admin disables requirements module
        self.client.login(email="superadmin_sync@platform.ai", password="Password123!")
        mod = BuyerModuleConfig.objects.get(module_key="my_requirements")
        toggle_res = self.client.post(reverse("admin-panel-buyer-module-toggle", kwargs={"pk": mod.pk}))
        self.assertEqual(toggle_res.status_code, 302)
        mod.refresh_from_db()
        self.assertFalse(mod.is_enabled)

        # Buyer attempts to access disabled module -> HTTP 403 PermissionDenied
        self.client.login(email="buyer_sync@buyer.com", password="Password123!")
        res_blocked = self.client.get(reverse("requirements-list"))
        self.assertEqual(res_blocked.status_code, 403)

        # Re-enable module -> HTTP 200 again
        self.client.login(email="superadmin_sync@platform.ai", password="Password123!")
        self.client.post(reverse("admin-panel-buyer-module-toggle", kwargs={"pk": mod.pk}))
        mod.refresh_from_db()
        self.assertTrue(mod.is_enabled)

        self.client.login(email="buyer_sync@buyer.com", password="Password123!")
        res_restored = self.client.get(reverse("requirements-list"))
        self.assertEqual(res_restored.status_code, 200)

    def test_seller_module_disable_enforces_backend_route_403(self):
        """Disabling a Seller module via Super Admin actively blocks backend route access with 403."""
        self.client.login(email="seller_sync@seller.com", password="Password123!")
        # Accessible initially
        res_initial = self.client.get(reverse("products-list"))
        self.assertEqual(res_initial.status_code, 200)

        # Super Admin disables products catalog module
        self.client.login(email="superadmin_sync@platform.ai", password="Password123!")
        mod = SellerModuleConfig.objects.get(module_key="my_products")
        toggle_res = self.client.post(reverse("admin-panel-seller-module-toggle", kwargs={"pk": mod.pk}))
        self.assertEqual(toggle_res.status_code, 302)
        mod.refresh_from_db()
        self.assertFalse(mod.is_enabled)

        # Seller attempts to access disabled module -> HTTP 403 PermissionDenied
        self.client.login(email="seller_sync@seller.com", password="Password123!")
        res_blocked = self.client.get(reverse("products-list"))
        self.assertEqual(res_blocked.status_code, 403)

        # Re-enable module -> HTTP 200 again
        self.client.login(email="superadmin_sync@platform.ai", password="Password123!")
        self.client.post(reverse("admin-panel-seller-module-toggle", kwargs={"pk": mod.pk}))
        mod.refresh_from_db()
        self.assertTrue(mod.is_enabled)

        self.client.login(email="seller_sync@seller.com", password="Password123!")
        res_restored = self.client.get(reverse("products-list"))
        self.assertEqual(res_restored.status_code, 200)

    def test_both_company_workspace_session_persistence(self):
        """BOTH companies support workspace switching and session persistence."""
        self.client.login(email="hybrid_sync@hybrid.com", password="Password123!")

        # 1. Switch to buyer workspace
        res = self.client.get(reverse("dashboard") + "?workspace=buyer")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.session.get("active_workspace"), "buyer")
        self.assertEqual(res.context["active_workspace"], "buyer")

        # 2. Subsequent dashboard access without query parameter retains 'buyer' workspace
        res2 = self.client.get(reverse("dashboard"))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.context["active_workspace"], "buyer")

        # 3. Switch to seller workspace
        res3 = self.client.get(reverse("dashboard") + "?workspace=seller")
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(self.client.session.get("active_workspace"), "seller")
        self.assertEqual(res3.context["active_workspace"], "seller")

    def test_admin_panel_users_export_all_and_company_wise(self):
        """Validates all users export, company-filtered export, and bulk selected export."""
        self.client.force_login(self.superadmin)

        # 1. Export All Users
        res_all = self.client.get(reverse("admin-panel-users") + "?export=csv")
        self.assertEqual(res_all.status_code, 200)
        self.assertIn("text/csv", res_all["Content-Type"])
        self.assertIn("Platform_Users_All_", res_all["Content-Disposition"])
        content_all = res_all.content.decode("utf-8")
        self.assertIn("superadmin_sync@platform.ai", content_all)
        self.assertIn("buyer_sync@buyer.com", content_all)

        # 2. Export Company-Wise Users
        res_comp = self.client.get(reverse("admin-panel-users") + f"?company_id={self.company.id}&export=csv")
        self.assertEqual(res_comp.status_code, 200)
        self.assertIn("text/csv", res_comp["Content-Type"])
        content_comp = res_comp.content.decode("utf-8")
        self.assertIn("buyer_sync@buyer.com", content_comp)

        # 3. Bulk Selected Users Export
        res_bulk = self.client.post(
            reverse("admin-panel-users"),
            {"bulk_action": "export", "selected_ids": [str(self.regular_user.id)]},
        )
        self.assertEqual(res_bulk.status_code, 200)
        self.assertIn("text/csv", res_bulk["Content-Type"])
        self.assertIn("Selected_Users_", res_bulk["Content-Disposition"])
        content_bulk = res_bulk.content.decode("utf-8")
        self.assertIn("buyer_sync@buyer.com", content_bulk)

    def test_admin_panel_module_exports_csv(self):
        """Validates CSV exports across companies, transactions, and audit logs."""
        self.client.force_login(self.superadmin)

        # Companies Export
        res_comp = self.client.get(reverse("admin-panel-companies") + "?export=csv")
        self.assertEqual(res_comp.status_code, 200)
        self.assertIn("text/csv", res_comp["Content-Type"])
        self.assertIn("Platform_Companies_", res_comp["Content-Disposition"])
        self.assertIn("Buyer Corp India", res_comp.content.decode("utf-8"))

        # Transactions Export (Wallet & Invoices)
        res_tx = self.client.get(reverse("admin-panel-transactions") + "?tab=wallet_tx&export=csv")
        self.assertEqual(res_tx.status_code, 200)
        self.assertIn("text/csv", res_tx["Content-Type"])
        self.assertIn("Platform_Wallet_Ledger_", res_tx["Content-Disposition"])

        # Audit Logs Export
        res_audit = self.client.get(reverse("admin-panel-auditlogs") + "?export=csv")
        self.assertEqual(res_audit.status_code, 200)
        self.assertIn("text/csv", res_audit["Content-Type"])
        self.assertIn("Platform_Audit_Trail_", res_audit["Content-Disposition"])

    def test_sidebar_no_duplicate_support_desk_for_superadmin(self):
        """Validates that Help & Support Desk appears only once in sidebar for super admin."""
        self.client.force_login(self.superadmin)
        res = self.client.get(reverse("admin-panel-dashboard"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")
        sidebar_part = html.split('<aside id="sidebar"')[1].split('</aside>')[0]
        count_in_sidebar = sidebar_part.count("/admin-panel/support/")
        self.assertEqual(count_in_sidebar, 1, f"Expected 1 support desk link in sidebar, found {count_in_sidebar}")

    def test_universal_export_parity_across_moderation_tables(self):
        """Validates that Products, Requirements, Wallets, Search Activity, Matching Criteria, and Roles all export to CSV."""
        self.client.force_login(self.superadmin)

        # 1. Products Catalog Export
        res_prd = self.client.get(reverse("admin-panel-products") + "?export=csv")
        self.assertEqual(res_prd.status_code, 200)
        self.assertIn("text/csv", res_prd["Content-Type"])
        self.assertIn("platform_products_catalog_", res_prd["Content-Disposition"])

        # 2. Buyer Requirements Export
        res_req = self.client.get(reverse("admin-panel-requirements") + "?export=csv")
        self.assertEqual(res_req.status_code, 200)
        self.assertIn("text/csv", res_req["Content-Type"])
        self.assertIn("platform_requirements_", res_req["Content-Disposition"])

        # 3. Company Wallets Export
        res_wal = self.client.get(reverse("admin-panel-wallets") + "?export=csv")
        self.assertEqual(res_wal.status_code, 200)
        self.assertIn("text/csv", res_wal["Content-Type"])
        self.assertIn("platform_company_wallets_", res_wal["Content-Disposition"])

        # 4. Search Operations Activity Export
        res_srch = self.client.get(reverse("admin-panel-search-activity") + "?export=csv")
        self.assertEqual(res_srch.status_code, 200)
        self.assertIn("text/csv", res_srch["Content-Type"])
        self.assertIn("platform_search_operations_", res_srch["Content-Disposition"])

        # 5. Matching Criteria Governance Export
        res_crit = self.client.get(reverse("admin-panel-matching-criteria") + "?export=csv")
        self.assertEqual(res_crit.status_code, 200)
        self.assertIn("text/csv", res_crit["Content-Type"])
        self.assertIn("platform_matching_criteria_", res_crit["Content-Disposition"])

        # 6. Roles & Permissions Matrix Export
        res_roles = self.client.get(reverse("admin-panel-roles") + "?export=csv")
        self.assertEqual(res_roles.status_code, 200)
        self.assertIn("text/csv", res_roles["Content-Type"])
        self.assertIn("platform_rbac_roles_matrix_", res_roles["Content-Disposition"])

    def test_tenant_impersonation_lifecycle(self):
        """Super Admin can jump into company workspace with 1 click and safely return."""
        self.client.force_login(self.superadmin)

        # 1. Impersonate company
        res_imp = self.client.get(reverse("admin-panel-impersonate-company", kwargs={"company_id": self.company.id}), follow=True)
        self.assertEqual(res_imp.status_code, 200)
        self.assertEqual(self.client.session.get("active_company_id"), self.company.id)
        self.assertTrue(self.client.session.get("impersonating_superadmin"))
        self.assertEqual(self.client.session.get("impersonating_company_name"), self.company.name)

        # Verify impersonation top banner rendered
        self.assertContains(res_imp, "Super Admin Impersonation Active")

        # 2. Exit impersonation
        res_exit = self.client.get(reverse("admin-panel-exit-impersonation"), follow=True)
        self.assertEqual(res_exit.status_code, 200)
        self.assertIsNone(self.client.session.get("impersonating_superadmin"))
        self.assertIsNone(self.client.session.get("active_company_id"))

    def test_predelete_impact_summary_and_soft_delete_guarantee(self):
        """Validates that impact summary API provides live counts and delete action is strictly soft-delete."""
        self.client.force_login(self.superadmin)

        # 1. Impact Summary API
        res_impact = self.client.get(reverse("admin-panel-company-impact-summary-api", kwargs={"company_id": self.company.id}))
        self.assertEqual(res_impact.status_code, 200)
        data = res_impact.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["company_name"], self.company.name)
        self.assertIn("members_count", data)
        self.assertIn("wallet_balance", data)
        self.assertIn("products_count", data)

        # 2. Soft-delete Company
        res_del = self.client.post(reverse("admin-panel-company-delete", kwargs={"pk": self.company.id}), follow=True)
        self.assertEqual(res_del.status_code, 200)

        # CRITICAL VERIFICATION: Soft-delete only, never hard deleted!
        self.company.refresh_from_db()
        self.assertTrue(self.company.is_deleted)
        self.assertFalse(self.company.is_active)
        self.assertIsNotNone(self.company.deleted_at)
        # Record still exists in database
        self.assertTrue(Company.objects.filter(id=self.company.id).exists())

    def test_matching_criteria_soft_delete_preservation(self):
        """Deleting a matching criterion sets is_active=False instead of database purge."""
        from apps.ai_search.models import PlatformMatchingCriteria
        self.client.force_login(self.superadmin)

        crit = PlatformMatchingCriteria.objects.create(
            name="Testing Soft Delete Criterion",
            parameter_key="test_soft_delete_param",
            weight_percentage=15,
            is_active=True,
        )

        res = self.client.post(
            reverse("admin-panel-matching-criteria"),
            {"action": "delete", "criteria_id": crit.id},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        # Verify record still exists in DB, but is soft-deleted (deactivated)
        crit.refresh_from_db()
        self.assertFalse(crit.is_active)
        self.assertTrue(PlatformMatchingCriteria.objects.filter(id=crit.id).exists())

    def test_credit_transaction_export_attribute_integrity(self):
        """Validates that Credit Transactions CSV export generates cleanly with ctx.credits."""
        self.client.force_login(self.superadmin)
        CreditTransaction.objects.create(
            company=self.buyer_comp,
            user=self.buyer_user,
            transaction_type=CreditTransaction.TransactionType.DEDUCTION,
            credits=-15,
            notes="AI Supplier Discovery test run",
        )
        res = self.client.get(reverse("admin-panel-transactions") + "?tab=credits&export=csv")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res["Content-Type"])
        self.assertIn("Platform_Credit_Ledger_", res["Content-Disposition"])
        content = res.content.decode("utf-8")
        self.assertIn("-15", content)
        self.assertIn("AI Supplier Discovery test run", content)

    def test_audit_logs_includes_suspended_companies_in_filter(self):
        """Suspended companies (is_active=False, is_deleted=False) must remain available in audit log filter."""
        self.client.force_login(self.superadmin)
        self.seller_comp.is_active = False
        self.seller_comp.save()

        res = self.client.get(reverse("admin-panel-auditlogs"))
        self.assertEqual(res.status_code, 200)
        companies_in_context = res.context["companies"]
        self.assertIn(self.seller_comp, companies_in_context)

    def test_central_reports_exports_products_requirements_wallets(self):
        """Validates centralized exports for products, requirements, and wallets in reports hub."""
        self.client.force_login(self.superadmin)

        # 1. Products CSV
        res_prd = self.client.get(reverse("admin-panel-reports") + "?export=products_csv")
        self.assertEqual(res_prd.status_code, 200)
        self.assertIn("Platform_Products_Catalog_", res_prd["Content-Disposition"])

        # 2. Requirements CSV
        res_req = self.client.get(reverse("admin-panel-reports") + "?export=requirements_csv")
        self.assertEqual(res_req.status_code, 200)
        self.assertIn("Platform_Requirements_RFQs_", res_req["Content-Disposition"])

        # 3. Wallets CSV
        res_wal = self.client.get(reverse("admin-panel-reports") + "?export=wallets_csv")
        self.assertEqual(res_wal.status_code, 200)
        self.assertIn("Platform_Company_Wallets_", res_wal["Content-Disposition"])

    def test_dashboards_batch_query_optimization_and_annotations(self):
        """Validates that Buyer, Seller, and Both governance dashboards compute annotated metrics efficiently."""
        self.client.force_login(self.superadmin)

        # Buyer Dashboard
        res_buyer = self.client.get(reverse("admin-panel-buyer-dashboard"))
        self.assertEqual(res_buyer.status_code, 200)
        self.assertIn("annotated_companies", res_buyer.context)

        # Seller Dashboard
        res_seller = self.client.get(reverse("admin-panel-seller-dashboard"))
        self.assertEqual(res_seller.status_code, 200)
        self.assertIn("annotated_companies", res_seller.context)

        # Both Dashboard
        res_both = self.client.get(reverse("admin-panel-both-dashboard"))
        self.assertEqual(res_both.status_code, 200)
        self.assertIn("annotated_companies", res_both.context)

    def test_transactions_date_range_filtering(self):
        """Validates that date horizon filtering correctly restricts transactions querysets."""
        from django.utils import timezone
        from datetime import timedelta
        from apps.billing.models import WalletTransaction, Invoice, CompanyWallet

        self.client.force_login(self.superadmin)
        now = timezone.now()

        # Create a wallet for seller_comp inline (setUp only creates one for buyer_comp)
        seller_wallet, _ = CompanyWallet.objects.get_or_create(
            company=self.seller_comp,
            defaults={"available_balance": Decimal("5000.00")},
        )

        # Create old transaction (60 days ago) and recent transaction (1 day ago)
        old_tx = WalletTransaction.objects.create(
            wallet=seller_wallet,
            company=self.seller_comp,
            transaction_type=WalletTransaction.TransactionType.RECHARGE,
            amount=Decimal("100.00"),
            balance_after=Decimal("100.00"),
            status=WalletTransaction.TransactionStatus.COMPLETED,
            description="Test old recharge",
            reference_id="OLD-TX-001",
        )
        WalletTransaction.objects.filter(id=old_tx.id).update(created_at=now - timedelta(days=60))

        recent_tx = WalletTransaction.objects.create(
            wallet=seller_wallet,
            company=self.seller_comp,
            transaction_type=WalletTransaction.TransactionType.RECHARGE,
            amount=Decimal("200.00"),
            balance_after=Decimal("300.00"),
            status=WalletTransaction.TransactionStatus.COMPLETED,
            description="Test recent recharge",
            reference_id="REC-TX-002",
        )

        # 1. 7 Days Filter should include recent_tx and exclude old_tx
        res_7d = self.client.get(reverse("admin-panel-transactions") + "?tab=wallet_tx&range=7d")
        self.assertEqual(res_7d.status_code, 200)
        page_ids = [tx.id for tx in res_7d.context["page_obj"]]
        self.assertIn(recent_tx.id, page_ids)
        self.assertNotIn(old_tx.id, page_ids)

        # 2. All Time Filter should include both
        res_all = self.client.get(reverse("admin-panel-transactions") + "?tab=wallet_tx&range=all")
        self.assertEqual(res_all.status_code, 200)
        page_ids_all = [tx.id for tx in res_all.context["page_obj"]]
        self.assertIn(recent_tx.id, page_ids_all)
        self.assertIn(old_tx.id, page_ids_all)

    def test_ai_search_job_retry_action(self):
        """Super Admin can retry a failed search job to reset it to QUEUED status."""
        self.client.force_login(self.superadmin)

        failed_job = SearchJob.objects.create(
            company=self.seller_comp,
            job_type=SearchJob.JobType.FIND_BUYERS,
            search_query="Automated Industrial Pumps",
            status=SearchJob.Status.FAILED,
            error_message="Gateway timeout: Google API 504",
        )

        res = self.client.post(
            reverse("admin-panel-search-activity"),
            {"action": "retry_job", "job_id": failed_job.id},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        failed_job.refresh_from_db()
        self.assertEqual(failed_job.status, SearchJob.Status.QUEUED)
        self.assertEqual(failed_job.error_message, "")
        self.assertEqual(failed_job.progress_percent, 0)

    def test_forensic_audit_log_client_ip_enrichment(self):
        """Administrative actions automatically append client IP address to audit descriptions."""
        self.client.force_login(self.superadmin)
        test_ip = "198.51.100.42"

        res = self.client.post(
            reverse("admin-panel-toggle-company-status", kwargs={"pk": self.seller_comp.pk}),
            follow=True,
            REMOTE_ADDR=test_ip,
        )
        self.assertEqual(res.status_code, 200)

        latest_log = ActivityLog.objects.filter(company=self.seller_comp).order_by("-created_at").first()
        self.assertIsNotNone(latest_log)
        self.assertIn(f"[IP: {test_ip}]", latest_log.description)


