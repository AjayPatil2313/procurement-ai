from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.utils import generate_password_reset_token
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

    def test_login_screen_renders_and_authenticates(self):
        """Verify login screen renders, accepts valid credentials, rejects invalid ones, and does not show quick demo logins"""
        # 1. GET login screen renders properly
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sign In")
        self.assertContains(resp, "AI Procurement & Sales")
        self.assertContains(resp, "Forgot password?")
        self.assertNotContains(resp, "Quick 1-Click")
        self.assertNotContains(resp, "Demo Logins")

        # 2. Invalid login fails
        resp_bad = self.client.post(reverse("login"), {"email": "admin@abctrading.com", "password": "WrongPassword"})
        self.assertEqual(resp_bad.status_code, 200)
        self.assertContains(resp_bad, "Invalid email or password")

        # 3. Valid login redirects to dashboard
        resp_good = self.client.post(reverse("login"), {"email": "admin@abctrading.com", "password": "Password@123"})
        self.assertEqual(resp_good.status_code, 302)
        self.assertIn("/dashboard", resp_good.url)

    def test_logout_screen_renders(self):
        """Verify logging out clears session and displays dedicated logout confirmation screen"""
        self.client.login(email="admin@abctrading.com", password="Password@123")
        resp = self.client.get(reverse("logout-web"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Logged Out Successfully")
        self.assertContains(resp, "Sign In Again")

    def test_root_redirect_flow(self):
        """Root URL redirects unauthenticated visitors to login screen, and authenticated users to dashboard"""
        # Unauthenticated: goes to login
        resp_unauth = self.client.get(reverse("root-redirect"))
        self.assertEqual(resp_unauth.status_code, 302)
        self.assertIn("/login", resp_unauth.url)

        # Authenticated: goes to dashboard
        self.client.login(email="admin@abctrading.com", password="Password@123")
        resp_auth = self.client.get(reverse("root-redirect"))
        self.assertEqual(resp_auth.status_code, 302)
        self.assertIn("/dashboard", resp_auth.url)

    def test_register_screen_and_full_authentication_authorization(self):
        """Verify registration screen, input validations, account creation, and Company Admin authorization"""
        # 1. GET registration screen
        resp = self.client.get(reverse("register"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Create Company Account")
        self.assertContains(resp, "Company Name")
        self.assertContains(resp, "Company Role")

        # 2. Signup alias redirects to register
        resp_signup = self.client.get(reverse("signup"))
        self.assertEqual(resp_signup.status_code, 302)
        self.assertIn("/register", resp_signup.url)

        # 3. Validation failure: password mismatch
        resp_mismatch = self.client.post(reverse("register"), {
            "first_name": "Rohan",
            "last_name": "Sharma",
            "email": "rohan@newcorp.com",
            "password": "Password@123",
            "confirm_password": "DifferentPassword@123",
            "company_name": "New Corp Ltd",
            "company_type": "BOTH",
        })
        self.assertEqual(resp_mismatch.status_code, 200)
        self.assertContains(resp_mismatch, "Passwords do not match")

        # 4. Successful registration & automatic authorization
        resp_success = self.client.post(reverse("register"), {
            "first_name": "Rohan",
            "last_name": "Kapoor",
            "email": "rohan@newcorp.com",
            "phone": "+91 99887 76655",
            "password": "Password@123",
            "confirm_password": "Password@123",
            "company_name": "Kapoor Technologies Pvt. Ltd.",
            "company_type": "BUYER",
            "industry": "Manufacturing",
            "company_size": "51-200",
            "city": "Bengaluru",
            "state": "Karnataka",
            "country": "India",
            "preferred_currency": "INR",
        })
        self.assertEqual(resp_success.status_code, 302)
        self.assertIn("/dashboard", resp_success.url)

        # 5. Verify created user, company, membership, and subscription
        new_user = User.objects.get(email="rohan@newcorp.com")
        self.assertEqual(new_user.first_name, "Rohan")
        self.assertEqual(new_user.last_name, "Kapoor")
        self.assertTrue(new_user.is_active)

        new_comp = Company.objects.get(name="Kapoor Technologies Pvt. Ltd.")
        self.assertEqual(new_comp.company_type, "BUYER")
        self.assertEqual(new_comp.created_by, new_user)

        membership = CompanyMember.objects.get(user=new_user, company=new_comp)
        self.assertEqual(membership.role, CompanyMember.Role.ADMIN)

        sub = Subscription.objects.get(company=new_comp)
        self.assertEqual(sub.credits_total, 100)
        self.assertEqual(sub.credits_remaining, 100)

        # 6. Verify dashboard displays new user and authorized workspace
        resp_dash = self.client.get(reverse("dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "Kapoor Technologies Pvt. Ltd.")
        self.assertContains(resp_dash, "Rohan Kapoor")
        self.assertContains(resp_dash, "Company Admin")

    def test_forgot_and_reset_password_flow(self):
        """Verify complete forgot password link generation, token validation, and password reset flow"""
        # 1. GET forgot-password page
        resp_get = self.client.get(reverse("forgot-password"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Forgot Password")
        self.assertContains(resp_get, "Registered Email Address")

        # 2. POST with non-existent email displays safe confirmation
        resp_unknown = self.client.post(reverse("forgot-password"), {"email": "nobody@unknown.com"})
        self.assertEqual(resp_unknown.status_code, 200)
        self.assertContains(resp_unknown, "nobody@unknown.com")

        # 3. POST with active user generates reset token
        resp_known = self.client.post(reverse("forgot-password"), {"email": "admin@abctrading.com"})
        self.assertEqual(resp_known.status_code, 200)
        self.assertContains(resp_known, "admin@abctrading.com")
        self.assertContains(resp_known, "/reset-password/")

        # 4. Generate valid token and test GET reset-password page
        token = generate_password_reset_token(self.company_admin)
        resp_reset_page = self.client.get(reverse("reset-password", kwargs={"token": token}))
        self.assertEqual(resp_reset_page.status_code, 200)
        self.assertContains(resp_reset_page, "Set New Password")
        self.assertContains(resp_reset_page, "admin@abctrading.com")

        # 5. Invalid token renders error
        resp_bad_token = self.client.get(reverse("reset-password", kwargs={"token": "invalid-token-123"}))
        self.assertEqual(resp_bad_token.status_code, 200)
        self.assertContains(resp_bad_token, "Invalid or corrupted password reset link")

        # 6. POST with password mismatch fails
        resp_mismatch = self.client.post(reverse("reset-password", kwargs={"token": token}), {
            "new_password": "NewStrongPassword@123",
            "confirm_password": "DifferentPassword@123",
        })
        self.assertEqual(resp_mismatch.status_code, 200)
        self.assertContains(resp_mismatch, "Passwords do not match")

        # 7. POST with password too short fails
        resp_short = self.client.post(reverse("reset-password", kwargs={"token": token}), {
            "new_password": "123",
            "confirm_password": "123",
        })
        self.assertEqual(resp_short.status_code, 200)
        self.assertContains(resp_short, "Password must be at least 6 characters")

        # 8. Successful password reset redirects to login
        resp_success = self.client.post(reverse("reset-password", kwargs={"token": token}), {
            "new_password": "UpdatedPassword@456",
            "confirm_password": "UpdatedPassword@456",
        })
        self.assertEqual(resp_success.status_code, 302)
        self.assertIn("/login", resp_success.url)

        # 9. Verify user can log in with new password and old password fails
        login_old = self.client.post(reverse("login"), {
            "email": "admin@abctrading.com",
            "password": "Password@123",
        })
        self.assertEqual(login_old.status_code, 200)
        self.assertContains(login_old, "Invalid email or password")

        login_new = self.client.post(reverse("login"), {
            "email": "admin@abctrading.com",
            "password": "UpdatedPassword@456",
        })
        self.assertEqual(login_new.status_code, 302)
        self.assertIn("/dashboard", login_new.url)


class DynamicCompanyTypeAndRBACTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Buyer Company & Users
        self.buyer_admin = User.objects.create_user(
            email="admin@apexprocure.com",
            password="Password@123",
            first_name="Anil",
            last_name="Verma",
        )
        self.buyer_company = Company.objects.create(
            name="Apex Procurement Pvt. Ltd.",
            company_type=Company.CompanyType.BUYER,
            created_by=self.buyer_admin,
        )
        CompanyMember.objects.create(
            user=self.buyer_admin,
            company=self.buyer_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )
        self.buyer_employee = User.objects.create_user(
            email="rahul@apexprocure.com",
            password="Password@123",
            first_name="Rahul",
            last_name="Sharma",
        )
        self.buyer_employee_member = CompanyMember.objects.create(
            user=self.buyer_employee,
            company=self.buyer_company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

        # 2. Seller Company & Users
        self.seller_admin = User.objects.create_user(
            email="admin@zenithsupply.com",
            password="Password@123",
            first_name="Sanjay",
            last_name="Gupta",
        )
        self.seller_company = Company.objects.create(
            name="Zenith Suppliers Pvt. Ltd.",
            company_type=Company.CompanyType.SELLER,
            created_by=self.seller_admin,
        )
        CompanyMember.objects.create(
            user=self.seller_admin,
            company=self.seller_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 3. Both Company & Users
        self.both_admin = User.objects.create_user(
            email="admin@globalnexus.com",
            password="Password@123",
            first_name="Neha",
            last_name="Singh",
        )
        self.both_company = Company.objects.create(
            name="Global Nexus Traders",
            company_type=Company.CompanyType.BOTH,
            created_by=self.both_admin,
        )
        CompanyMember.objects.create(
            user=self.both_admin,
            company=self.both_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Sample requirement and product
        self.sample_req = Requirement.objects.create(
            company=self.buyer_company,
            created_by=self.buyer_admin,
            item_name="Industrial Bearings",
            quantity=100,
        )
        self.sample_prod = Product.objects.create(
            company=self.seller_company,
            name="CNC Milling Machine",
            price_min=50000,
            price_max=80000,
        )

    def test_buyer_company_module_and_api_isolation(self):
        """Buyer company sees Buyer modules only, Buyer APIs allowed, Seller APIs blocked (403)"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        # 1. Modules API shows only Buyer modules
        resp = self.client.get(reverse("company-modules"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["company_type"], "BUYER")
        self.assertTrue(data["can_view_buyer"])
        self.assertFalse(data["can_view_seller"])
        self.assertTrue(data["is_buyer_only"])
        self.assertIn("my_requirements", data["visible_modules"])
        self.assertNotIn("my_products", data["visible_modules"])
        self.assertNotIn("buyer_leads", data["visible_modules"])

        # 2. Sidebar HTML check: shows BUYER section, hides SELLER section
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "BUYER")
        self.assertContains(dash_resp, "My Requirements")
        self.assertContains(dash_resp, "Buyer Dashboard")
        self.assertNotContains(dash_resp, "SELLER")
        self.assertNotContains(dash_resp, "My Products")

        # 3. Buyer API allowed: GET /api/requirements/
        req_api_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(req_api_resp.status_code, 200)
        self.assertEqual(len(req_api_resp.json()), 1)

        # 4. Seller API blocked: GET /api/catalog/products/ -> 403 Forbidden
        prod_api_resp = self.client.get(reverse("api-products-list"))
        self.assertEqual(prod_api_resp.status_code, 403)

        # 5. Seller web view blocked: /sales/products/ -> redirects to dashboard
        sales_web_resp = self.client.get(reverse("products-list"))
        self.assertEqual(sales_web_resp.status_code, 302)
        self.assertIn("/dashboard", sales_web_resp.url)

    def test_seller_company_module_and_api_isolation(self):
        """Seller company sees Seller modules only, Seller APIs allowed, Buyer APIs blocked (403)"""
        self.client.login(email="admin@zenithsupply.com", password="Password@123")

        # 1. Modules API shows only Seller modules
        resp = self.client.get(reverse("company-modules"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["company_type"], "SELLER")
        self.assertFalse(data["can_view_buyer"])
        self.assertTrue(data["can_view_seller"])
        self.assertTrue(data["is_seller_only"])
        self.assertIn("my_products", data["visible_modules"])
        self.assertNotIn("my_requirements", data["visible_modules"])

        # 2. Sidebar HTML check: shows SELLER section, hides BUYER section
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "SELLER")
        self.assertContains(dash_resp, "My Products")
        self.assertContains(dash_resp, "Seller Dashboard")
        self.assertNotContains(dash_resp, "BUYER")
        self.assertNotContains(dash_resp, "My Requirements")

        # 3. Seller API allowed: GET /api/catalog/products/
        prod_api_resp = self.client.get(reverse("api-products-list"))
        self.assertEqual(prod_api_resp.status_code, 200)
        self.assertEqual(len(prod_api_resp.json()), 1)

        # 4. Buyer API blocked: GET /api/requirements/ -> 403 Forbidden
        req_api_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(req_api_resp.status_code, 403)

        # 5. Buyer web view blocked: /procurement/requirements/ -> redirects to dashboard
        buyer_web_resp = self.client.get(reverse("requirements-list"))
        self.assertEqual(buyer_web_resp.status_code, 302)
        self.assertIn("/dashboard", buyer_web_resp.url)

    def test_both_company_access(self):
        """BOTH company sees both Buyer and Seller modules, both APIs allowed"""
        self.client.login(email="admin@globalnexus.com", password="Password@123")

        # 1. Modules API shows both
        resp = self.client.get(reverse("company-modules"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["company_type"], "BOTH")
        self.assertTrue(data["can_view_buyer"])
        self.assertTrue(data["can_view_seller"])
        self.assertTrue(data["is_both"])

        # 2. Sidebar shows both BUYER and SELLER
        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "BUYER")
        self.assertContains(dash_resp, "SELLER")

        # 3. Both APIs work
        req_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(req_resp.status_code, 200)

        prod_resp = self.client.get(reverse("api-products-list"))
        self.assertEqual(prod_resp.status_code, 200)

    def test_company_admin_add_user_and_assign_permissions(self):
        """Company Admin creates user and assigns granular permissions via API"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        # 1. Admin creates new team member
        create_resp = self.client.post(reverse("company-users-create"), {
            "email": "priya.analyst@apexprocure.com",
            "password": "Password@123",
            "first_name": "Priya",
            "last_name": "Analyst",
        }, content_type="application/json")
        self.assertEqual(create_resp.status_code, 201)
        new_user_id = create_resp.json()["user"]["id"]

        # 2. Regular user cannot create other users
        self.client.login(email="rahul@apexprocure.com", password="Password@123")
        forbidden_create = self.client.post(reverse("company-users-create"), {
            "email": "hacker@test.com",
            "password": "Password@123",
        }, content_type="application/json")
        self.assertEqual(forbidden_create.status_code, 403)

        # 3. Admin assigns READ and EDIT permissions to the new user
        self.client.login(email="admin@apexprocure.com", password="Password@123")
        assign_resp = self.client.post(reverse("company-users-permissions-assign"), {
            "user_id": new_user_id,
            "permission_codes": ["READ", "EDIT"],
        }, content_type="application/json")
        self.assertEqual(assign_resp.status_code, 200)

        # 4. Verify user permissions endpoint
        perm_resp = self.client.get(reverse("company-user-permissions-detail", kwargs={"user_id": new_user_id}))
        self.assertEqual(perm_resp.status_code, 200)

    def test_granular_rbac_allowed_read_edit_blocked_update_delete(self):
        """
        Rahul Sharma has Requirements READ + EDIT.
        Rahul CAN:
          - GET /api/requirements/ (READ)
          - POST /api/requirements/ (EDIT)
        Rahul CANNOT:
          - PUT /api/requirements/<id>/ (UPDATE) -> 403 Forbidden
          - DELETE /api/requirements/<id>/ (DELETE) -> 403 Forbidden
        """
        # Assign READ and EDIT to Rahul
        perm_read, _ = CompanyPermission.objects.get_or_create(module="requirements", permission="READ")
        perm_edit, _ = CompanyPermission.objects.get_or_create(module="requirements", permission="EDIT")
        MemberPermission.objects.create(member=self.buyer_employee_member, permission=perm_read)
        MemberPermission.objects.create(member=self.buyer_employee_member, permission=perm_edit)

        self.client.login(email="rahul@apexprocure.com", password="Password@123")

        # 1. READ is allowed
        get_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(get_resp.status_code, 200)

        # 2. EDIT is allowed (POST)
        post_resp = self.client.post(reverse("api-requirements-list"), {
            "item_name": "New Hydraulic Pump",
            "quantity": 25,
            "unit": "pcs",
        }, content_type="application/json")
        self.assertEqual(post_resp.status_code, 201)
        created_id = post_resp.json()["id"]

        # 3. UPDATE is blocked (PUT) -> 403 Forbidden
        put_resp = self.client.put(reverse("api-requirements-detail", kwargs={"pk": created_id}), {
            "item_name": "Updated Pump Name",
            "quantity": 50,
        }, content_type="application/json")
        self.assertEqual(put_resp.status_code, 403)

        # 4. DELETE is blocked (DELETE) -> 403 Forbidden
        del_resp = self.client.delete(reverse("api-requirements-detail", kwargs={"pk": created_id}))
        self.assertEqual(del_resp.status_code, 403)

    def test_login_page_toggle_company_and_user(self):
        """Test login page toggle between Company Login and User Login, and authentication for both"""
        # 1. Default GET /login/ has Company Login & User Login tabs, Sign Up link
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Company Login")
        self.assertContains(resp, "User Login")
        self.assertContains(resp, "Sign Up")
        self.assertEqual(resp.context["login_type"], "company")

        # 2. GET /login/?type=user loads with user mode in context
        user_resp = self.client.get(reverse("login") + "?type=user")
        self.assertEqual(user_resp.status_code, 200)
        self.assertEqual(user_resp.context["login_type"], "user")
        self.assertContains(user_resp, "User Login")

        # 3. Authenticate as Company User (Rahul)
        user_login = self.client.post(reverse("login"), {
            "email": "rahul@apexprocure.com",
            "password": "Password@123",
            "login_type": "user",
        })
        self.assertEqual(user_login.status_code, 302)
        self.assertIn("/dashboard", user_login.url)
        self.client.logout()

        # 4. Authenticate as Company Admin (Anil)
        admin_login = self.client.post(reverse("login"), {
            "email": "admin@apexprocure.com",
            "password": "Password@123",
            "login_type": "company",
        })
        self.assertEqual(admin_login.status_code, 302)
        self.assertIn("/dashboard", admin_login.url)

    def test_company_admin_create_user_with_full_fields_api(self):
        """Company Admin creates user via API with email, password, first_name, last_name, phone, is_email_verified, is_active"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        create_resp = self.client.post(reverse("company-users-create"), {
            "email": "priya.engineer@apexprocure.com",
            "password": "SecurePass@789",
            "first_name": "Priya",
            "last_name": "Patel",
            "phone": "+919876543210",
            "is_email_verified": True,
            "is_active": True,
        }, content_type="application/json")

        self.assertEqual(create_resp.status_code, 201)
        resp_data = create_resp.json()
        self.assertEqual(resp_data["user"]["email"], "priya.engineer@apexprocure.com")
        self.assertEqual(resp_data["user"]["first_name"], "Priya")
        self.assertEqual(resp_data["user"]["last_name"], "Patel")
        self.assertEqual(resp_data["user"]["phone"], "+919876543210")
        self.assertTrue(resp_data["user"]["is_email_verified"])
        self.assertTrue(resp_data["user"]["is_active"])

        # Check DB
        priya = User.objects.get(email="priya.engineer@apexprocure.com")
        self.assertEqual(priya.phone, "+919876543210")
        self.assertTrue(priya.is_email_verified)
        self.assertTrue(priya.is_active)
        self.assertTrue(priya.check_password("SecurePass@789"))
        self.assertEqual(priya.company_membership.role, CompanyMember.Role.USER)
        self.assertEqual(priya.company_membership.company, self.buyer_company)

    def test_company_admin_create_user_web_view(self):
        """Company Admin creates user via Web Form (/company/team/) with all fields"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        web_resp = self.client.post(reverse("company-team-web"), {
            "action": "add_member",
            "email": "karan.sales@apexprocure.com",
            "password": "KaranPass@123",
            "first_name": "Karan",
            "last_name": "Mehta",
            "phone": "+919123456789",
            "role": "USER",
            "is_email_verified": "on",
            "is_active": "on",
        })
        self.assertEqual(web_resp.status_code, 302)
        self.assertIn("/company/team/", web_resp.url)

        # Check DB
        karan = User.objects.get(email="karan.sales@apexprocure.com")
        self.assertEqual(karan.first_name, "Karan")
        self.assertEqual(karan.last_name, "Mehta")
        self.assertEqual(karan.phone, "+919123456789")
        self.assertTrue(karan.is_email_verified)
        self.assertTrue(karan.is_active)
        self.assertTrue(karan.check_password("KaranPass@123"))
        self.assertEqual(karan.company_membership.role, CompanyMember.Role.USER)
        self.assertEqual(karan.company_membership.company, self.buyer_company)

    def test_company_admin_edit_user_web_view(self):
        """Company Admin edits user name, phone, and password via Web Form"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        # Edit Rahul Sharma
        edit_resp = self.client.post(reverse("company-team-web"), {
            "action": "edit_member",
            "member_id": self.buyer_employee_member.id,
            "first_name": "Rahul (Senior)",
            "last_name": "Sharma",
            "phone": "+91 99988 77766",
            "password": "NewRahulPassword@123",
            "role": "USER",
            "is_active": "on",
            "is_email_verified": "on",
        })
        self.assertEqual(edit_resp.status_code, 302)

        # Verify DB updates
        self.buyer_employee.refresh_from_db()
        self.assertEqual(self.buyer_employee.first_name, "Rahul (Senior)")
        self.assertEqual(self.buyer_employee.phone, "+91 99988 77766")
        self.assertTrue(self.buyer_employee.check_password("NewRahulPassword@123"))

    def test_company_admin_activate_deactivate_user(self):
        """Company Admin can activate / deactivate users; deactivated users cannot log in"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        # 1. Deactivate Rahul
        deact_resp = self.client.post(reverse("company-team-web"), {
            "action": "toggle_status",
            "member_id": self.buyer_employee_member.id,
        })
        self.assertEqual(deact_resp.status_code, 302)

        self.buyer_employee.refresh_from_db()
        self.buyer_employee_member.refresh_from_db()
        self.assertFalse(self.buyer_employee.is_active)
        self.assertFalse(self.buyer_employee_member.is_active)

        # 2. Rahul attempts login while deactivated -> blocked
        self.client.logout()
        blocked_login = self.client.post(reverse("login"), {
            "email": "rahul@apexprocure.com",
            "password": "Password@123",
            "login_type": "user",
        })
        self.assertEqual(blocked_login.status_code, 200)
        self.assertContains(blocked_login, "inactive")

        # 3. Admin reactivates Rahul
        self.client.login(email="admin@apexprocure.com", password="Password@123")
        react_resp = self.client.post(reverse("company-team-web"), {
            "action": "toggle_status",
            "member_id": self.buyer_employee_member.id,
        })
        self.assertEqual(react_resp.status_code, 302)

        self.buyer_employee.refresh_from_db()
        self.buyer_employee_member.refresh_from_db()
        self.assertTrue(self.buyer_employee.is_active)
        self.assertTrue(self.buyer_employee_member.is_active)

        # 4. Rahul can now log in successfully
        self.client.logout()
        ok_login = self.client.post(reverse("login"), {
            "email": "rahul@apexprocure.com",
            "password": "Password@123",
            "login_type": "user",
        })
        self.assertEqual(ok_login.status_code, 302)
        self.assertIn("/dashboard", ok_login.url)

        # 5. Admin cannot deactivate themselves
        self.client.login(email="admin@apexprocure.com", password="Password@123")
        admin_member = CompanyMember.objects.get(user=self.buyer_admin, company=self.buyer_company)
        self_deact = self.client.post(reverse("company-team-web"), {
            "action": "toggle_status",
            "member_id": admin_member.id,
        })
        self.assertEqual(self_deact.status_code, 302)
        self.buyer_admin.refresh_from_db()
        self.assertTrue(self.buyer_admin.is_active)

    def test_company_admin_user_detail_update_api(self):
        """Company Admin manages user detail, update, and deactivation via REST API"""
        self.client.login(email="admin@apexprocure.com", password="Password@123")

        # 1. GET user detail API
        detail_resp = self.client.get(reverse("company-user-detail-update", kwargs={"user_id": self.buyer_employee.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["email"], "rahul@apexprocure.com")

        # 2. PATCH user API (update phone and name)
        patch_resp = self.client.patch(
            reverse("company-user-detail-update", kwargs={"user_id": self.buyer_employee.id}),
            {"phone": "+91 88877 66655", "first_name": "Rahul Updated"},
            content_type="application/json"
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.buyer_employee.refresh_from_db()
        self.assertEqual(self.buyer_employee.phone, "+91 88877 66655")
        self.assertEqual(self.buyer_employee.first_name, "Rahul Updated")

        # 3. DELETE user API (deactivate)
        del_resp = self.client.delete(reverse("company-user-detail-update", kwargs={"user_id": self.buyer_employee.id}))
        self.assertEqual(del_resp.status_code, 200)
        self.buyer_employee.refresh_from_db()
        self.assertFalse(self.buyer_employee.is_active)






