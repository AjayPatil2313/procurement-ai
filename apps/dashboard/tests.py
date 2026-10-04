from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember, CompanyPermission, MemberPermission
from apps.billing.models import Subscription
from apps.requirements.models import Requirement
from apps.catalog.models import Product
from apps.ai_search.models import SearchJob


class RBACTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Super Admin
        self.superadmin = User.objects.create_superuser(
            email="superadmin@test.com",
            password="Password@123",
            first_name="Super",
            last_name="Admin",
        )

        # 2. Company
        self.company = Company.objects.create(
            name="ABC Trading Pvt. Ltd.",
            company_type=Company.CompanyType.BOTH,
            created_by=self.superadmin,
        )
        self.subscription = Subscription.objects.create(
            company=self.company,
            plan=Subscription.Plan.PRO,
            credits_total=500,
            credits_used=180,
        )

        # 3. Company Admin
        self.company_admin = User.objects.create_user(
            email="admin@abctrading.com",
            password="Password@123",
            first_name="Ajay",
            last_name="Patil",
        )
        CompanyMember.objects.create(
            user=self.company_admin,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 4. Company User (Buyer)
        self.buyer_user = User.objects.create_user(
            email="buyer@abctrading.com",
            password="Password@123",
            first_name="Rahul",
            last_name="Sharma",
        )
        self.buyer_member = CompanyMember.objects.create(
            user=self.buyer_user,
            company=self.company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

        # Assign Buyer permissions
        perm_req_read, _ = CompanyPermission.objects.get_or_create(module="requirements", permission="READ")
        perm_dash_read, _ = CompanyPermission.objects.get_or_create(module="dashboard", permission="READ")
        MemberPermission.objects.create(member=self.buyer_member, permission=perm_req_read)
        MemberPermission.objects.create(member=self.buyer_member, permission=perm_dash_read)

        # 5. Company User (Seller)
        self.seller_user = User.objects.create_user(
            email="seller@abctrading.com",
            password="Password@123",
            first_name="Priya",
            last_name="Patel",
        )
        self.seller_member = CompanyMember.objects.create(
            user=self.seller_user,
            company=self.company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )
        perm_prod_read, _ = CompanyPermission.objects.get_or_create(module="products", permission="READ")
        MemberPermission.objects.create(member=self.seller_member, permission=perm_prod_read)
        MemberPermission.objects.create(member=self.seller_member, permission=perm_dash_read)

        # 6. Create some sample requirements and search jobs
        Requirement.objects.create(
            company=self.company,
            created_by=self.company_admin,
            item_name="Industrial Pump",
            quantity=10,
            search_scope="global",
            status="completed",
        )

    def test_unauthenticated_redirect(self):
        """Unauthenticated user should be redirected to login"""
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_company_admin_dashboard_access(self):
        """Company Admin should successfully access dashboard with full controls"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "ABC Trading Pvt. Ltd.")
        self.assertContains(response, "Company Admin")
        self.assertContains(response, "Total Searches")
        self.assertContains(response, "Found Suppliers")
        self.assertContains(response, "Credits Remaining")

    def test_company_admin_team_management(self):
        """Company Admin can access team management"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        response = self.client.get(reverse("company-team-web"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team Members & Access Control")

    def test_company_admin_cannot_access_superadmin_pages(self):
        """Company Admin cannot access Super Admin pages"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        response = self.client.get(reverse("admin-panel-companies"))
        self.assertEqual(response.status_code, 403)

    def test_company_user_restricted_from_team_management(self):
        """Regular Company User is blocked from accessing Team Management"""
        self.client.login(email="buyer@abctrading.com", password="Password@123")
        response = self.client.get(reverse("company-team-web"))
        # Should redirect with message
        self.assertEqual(response.status_code, 302)

    def test_superadmin_full_access(self):
        """Super Admin has access to all companies admin panel"""
        self.client.login(email="superadmin@test.com", password="Password@123")
        response = self.client.get(reverse("admin-panel-companies"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform Companies")
        self.assertContains(response, "Super Admin Area")

    def test_dashboard_api(self):
        """API /dashboard/api/dashboard/ returns JSON metrics"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        response = self.client.get(reverse("api-dashboard"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metrics", data)
        self.assertIn("charts", data)
        self.assertIn("recent_searches", data)
        self.assertEqual(data["metrics"]["credits_remaining"], 320)
        self.assertEqual(data["company_summary"]["name"], "ABC Trading Pvt. Ltd.")

    def test_sales_leads_and_pipeline_removal(self):
        """Verify leads-list and saved-leads render properly, and lead pipeline is removed"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        
        # 1. Leads list works
        resp_leads = self.client.get(reverse("leads-list"))
        self.assertEqual(resp_leads.status_code, 200)
        self.assertContains(resp_leads, "Sales Leads")
        self.assertNotContains(resp_leads, "Lead Pipeline")

        # 2. Saved leads works
        resp_saved = self.client.get(reverse("saved-leads"))
        self.assertEqual(resp_saved.status_code, 200)
        self.assertContains(resp_saved, "Saved Leads")

        # 3. Pipeline route should not exist (404)
        resp_pipeline = self.client.get("/sales/pipeline/")
        self.assertEqual(resp_pipeline.status_code, 404)

    def test_navbar_single_role_and_person_name(self):
        """Verify navbar shows dynamic person name and strictly 1 correct role for each user type"""
        # 1. Company Admin
        self.client.login(email="admin@abctrading.com", password="Password@123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ajay Patil")
        self.assertContains(resp, "Company Admin")

        # 2. Buyer User
        self.client.login(email="buyer@abctrading.com", password="Password@123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Rahul Sharma")
        self.assertContains(resp, "Buyer")

        # 3. Seller User
        self.client.login(email="seller@abctrading.com", password="Password@123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Priya Patel")
        self.assertContains(resp, "Seller")

        # 4. Super Admin
        self.client.login(email="superadmin@test.com", password="Password@123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Super Admin")

    def test_global_search_working(self):
        """Verify the navbar global search returns matching results for query"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        
        # Search for existing item "Pump"
        resp = self.client.get(reverse("global-search"), {"q": "Pump"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Search Results for")
        self.assertContains(resp, "Pump")
        self.assertContains(resp, "Industrial Pump")

        # Search for non-existing query shows helpful fallback
        resp_empty = self.client.get(reverse("global-search"), {"q": "XYZ_NonExistent_Item_999"})
        self.assertEqual(resp_empty.status_code, 200)
        self.assertContains(resp_empty, "No direct matches found")

