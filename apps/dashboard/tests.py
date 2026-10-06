from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.utils import generate_password_reset_token
from apps.companies.models import Company, CompanyMember, CompanyPermission, MemberPermission
from apps.billing.models import Subscription
from apps.requirements.models import Requirement
from apps.catalog.models import Product, Category, ProductImage
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


class SellerModuleTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Company ABC (Seller)
        self.abc_admin = User.objects.create_user(
            email="admin@abctraders.com",
            password="Password@123",
            first_name="Ajay",
            last_name="Traders",
        )
        self.abc_company = Company.objects.create(
            name="ABC Traders",
            company_type=Company.CompanyType.SELLER,
            created_by=self.abc_admin,
            city="Pune",
        )
        CompanyMember.objects.create(
            user=self.abc_admin,
            company=self.abc_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # ABC Reader (Company User with only READ permission)
        self.abc_reader = User.objects.create_user(
            email="reader@abctraders.com",
            password="Password@123",
            first_name="Ramesh",
            last_name="Reader",
        )
        self.abc_reader_member = CompanyMember.objects.create(
            user=self.abc_reader,
            company=self.abc_company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )
        perm_read, _ = CompanyPermission.objects.get_or_create(module="products", permission="READ")
        MemberPermission.objects.create(member=self.abc_reader_member, permission=perm_read)

        # 2. Company XYZ (Seller)
        self.xyz_admin = User.objects.create_user(
            email="admin@xyztraders.com",
            password="Password@123",
            first_name="Xavier",
            last_name="Traders",
        )
        self.xyz_company = Company.objects.create(
            name="XYZ Traders",
            company_type=Company.CompanyType.SELLER,
            created_by=self.xyz_admin,
            city="Ahmedabad",
        )
        CompanyMember.objects.create(
            user=self.xyz_admin,
            company=self.xyz_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 3. Company Buyer (Buyer only)
        self.buyer_admin = User.objects.create_user(
            email="admin@buyeronly.com",
            password="Password@123",
            first_name="Bharat",
            last_name="Buyer",
        )
        self.buyer_company = Company.objects.create(
            name="Buyer Only Corp",
            company_type=Company.CompanyType.BUYER,
            created_by=self.buyer_admin,
        )
        CompanyMember.objects.create(
            user=self.buyer_admin,
            company=self.buyer_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Category
        self.category = Category.objects.create(name="Pumps & Valves", hsn_code="8413")

        # Existing Products:
        # ABC Traders: Product A, Product B
        self.prod_a = Product.objects.create(
            company=self.abc_company,
            name="Product A - ANSI Pump",
            category=self.category,
            price=15000.00,
            minimum_order_quantity=2,
            availability=Product.Availability.IN_STOCK,
            created_by=self.abc_admin,
        )
        self.prod_b = Product.objects.create(
            company=self.abc_company,
            name="Product B - Control Valve",
            category=self.category,
            price=8500.00,
            minimum_order_quantity=5,
            availability=Product.Availability.MADE_TO_ORDER,
            created_by=self.abc_admin,
        )

        # XYZ Traders: Product X
        self.prod_x = Product.objects.create(
            company=self.xyz_company,
            name="Product X - XYZ Boiler",
            category=self.category,
            price=95000.00,
            minimum_order_quantity=1,
            availability=Product.Availability.IN_STOCK,
            created_by=self.xyz_admin,
        )

    def test_seller_dashboard_view(self):
        """Seller Company Admin sees Seller Dashboard with real active products count"""
        self.client.login(email="admin@abctraders.com", password="Password@123")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seller Dashboard")
        self.assertContains(response, "Products Catalog")
        # ABC Traders has 2 active products
        self.assertContains(response, "2")
        # Buyer procurement section is not shown for seller-only company
        self.assertNotContains(response, "My Requirements")

    def test_seller_product_list_and_detail_web(self):
        """Seller can view their product list and product details"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        # Product list
        list_resp = self.client.get(reverse("products-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, "Product A - ANSI Pump")
        self.assertContains(list_resp, "Product B - Control Valve")
        # Multi-tenancy: XYZ's product X is NOT visible
        self.assertNotContains(list_resp, "Product X - XYZ Boiler")

        # Product detail
        detail_resp = self.client.get(reverse("product-detail", kwargs={"pk": self.prod_a.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Product A - ANSI Pump")
        self.assertContains(detail_resp, "15000.00")
        self.assertContains(detail_resp, "In Stock")

    def test_seller_product_create_web(self):
        """Seller Admin can add a new product to their catalog"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        post_data = {
            "name": "Product C - Industrial Flange",
            "category_id": self.category.id,
            "description": "High pressure carbon steel pipe flanges",
            "specifications": "Material: ASTM A105, Pressure Class: 300#",
            "price": "450.00",
            "currency": "INR",
            "minimum_order_quantity": "25",
            "unit": "pcs",
            "availability": "IN_STOCK",
            "location": "Pune, India",
            "image_url": "https://example.com/flange.jpg",
            "search_scope": "country",
        }
        create_resp = self.client.post(reverse("products-create"), post_data)
        self.assertEqual(create_resp.status_code, 302)

        new_prod = Product.objects.get(name="Product C - Industrial Flange")
        self.assertEqual(new_prod.company, self.abc_company)
        self.assertEqual(new_prod.created_by, self.abc_admin)
        self.assertEqual(float(new_prod.price), 450.00)
        self.assertEqual(float(new_prod.minimum_order_quantity), 25.0)
        self.assertFalse(new_prod.is_deleted)
        self.assertEqual(new_prod.images.count(), 1)
        self.assertEqual(new_prod.images.first().image_url, "https://example.com/flange.jpg")

    def test_seller_product_edit_web(self):
        """Seller Admin can update existing product specifications and price"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        edit_data = {
            "name": "Product A - ANSI Pump (Updated Edition)",
            "category_id": self.category.id,
            "description": "Updated high performance pump",
            "specifications": "Power: 75HP",
            "price": "18500.00",
            "currency": "INR",
            "minimum_order_quantity": "4",
            "unit": "sets",
            "availability": "MADE_TO_ORDER",
            "location": "Mumbai Factory",
        }
        edit_resp = self.client.post(reverse("product-edit", kwargs={"pk": self.prod_a.id}), edit_data)
        self.assertEqual(edit_resp.status_code, 302)

        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.name, "Product A - ANSI Pump (Updated Edition)")
        self.assertEqual(float(self.prod_a.price), 18500.00)
        self.assertEqual(float(self.prod_a.minimum_order_quantity), 4.0)
        self.assertEqual(self.prod_a.availability, Product.Availability.MADE_TO_ORDER)

    def test_seller_product_soft_delete_web(self):
        """Deleting a product performs a soft delete (is_deleted=True), never hard delete"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        delete_resp = self.client.post(reverse("product-delete", kwargs={"pk": self.prod_a.id}))
        self.assertEqual(delete_resp.status_code, 302)

        # Record still exists in MySQL DB!
        self.prod_a.refresh_from_db()
        self.assertTrue(self.prod_a.is_deleted)
        self.assertIsNotNone(self.prod_a.deleted_at)

        # Removed from product list context and table links
        list_resp = self.client.get(reverse("products-list"))
        self.assertNotIn(self.prod_a, list_resp.context["products"])
        self.assertNotContains(list_resp, f"/sales/products/{self.prod_a.id}/")

    def test_seller_multi_tenant_isolation(self):
        """Strict isolation: ABC Traders cannot view, edit, or delete XYZ's products"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        # 1. ABC cannot view XYZ's product X (404)
        detail_resp = self.client.get(reverse("product-detail", kwargs={"pk": self.prod_x.id}))
        self.assertEqual(detail_resp.status_code, 404)

        # 2. ABC cannot edit XYZ's product X (404)
        edit_resp = self.client.post(reverse("product-edit", kwargs={"pk": self.prod_x.id}), {
            "name": "Hacked Product",
            "price": "1.00",
        })
        self.assertEqual(edit_resp.status_code, 404)

        # 3. ABC cannot delete XYZ's product X (404)
        del_resp = self.client.post(reverse("product-delete", kwargs={"pk": self.prod_x.id}))
        self.assertEqual(del_resp.status_code, 404)
        self.prod_x.refresh_from_db()
        self.assertFalse(self.prod_x.is_deleted)

    def test_seller_product_api_crud_and_soft_delete(self):
        """REST API: Scoped list, create, detail, update, and soft-delete"""
        self.client.login(email="admin@abctraders.com", password="Password@123")

        # 1. GET products list API
        list_resp = self.client.get(reverse("api-products-list"))
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()
        item_names = [i["name"] for i in items]
        self.assertIn("Product A - ANSI Pump", item_names)
        self.assertIn("Product B - Control Valve", item_names)
        self.assertNotIn("Product X - XYZ Boiler", item_names)

        # 2. POST create product API
        post_resp = self.client.post(
            reverse("api-products-list"),
            {
                "name": "Product D - High Temp Gasket",
                "price": "120.00",
                "minimum_order_quantity": 50,
                "unit": "pcs",
                "availability": "IN_STOCK",
            },
            content_type="application/json",
        )
        self.assertEqual(post_resp.status_code, 201)
        new_id = post_resp.json()["id"]

        # 3. GET detail API
        detail_resp = self.client.get(reverse("api-products-detail", kwargs={"pk": new_id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["name"], "Product D - High Temp Gasket")

        # 4. PATCH update API
        patch_resp = self.client.patch(
            reverse("api-products-detail", kwargs={"pk": new_id}),
            {"price": "135.00"},
            content_type="application/json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(float(patch_resp.json()["price"]), 135.00)

        # 5. DELETE API (soft-delete)
        del_resp = self.client.delete(reverse("api-products-detail", kwargs={"pk": new_id}))
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.json()["is_deleted"])

        del_prod = Product.objects.get(id=new_id)
        self.assertTrue(del_prod.is_deleted)

    def test_seller_rbac_permission_checks(self):
        """Company User with only READ permission can view but cannot create, edit, or delete"""
        self.client.login(email="reader@abctraders.com", password="Password@123")

        # Can view list and detail
        self.assertEqual(self.client.get(reverse("products-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("product-detail", kwargs={"pk": self.prod_a.id})).status_code, 200)

        # Cannot create
        create_resp = self.client.post(reverse("products-create"), {"name": "Blocked Item"})
        self.assertEqual(create_resp.status_code, 302)

        # Cannot edit
        edit_resp = self.client.post(reverse("product-edit", kwargs={"pk": self.prod_a.id}), {"name": "Blocked Edit"})
        self.assertEqual(edit_resp.status_code, 302)

        # Cannot delete
        del_resp = self.client.post(reverse("product-delete", kwargs={"pk": self.prod_a.id}))
        self.assertEqual(del_resp.status_code, 302)
        self.prod_a.refresh_from_db()
        self.assertFalse(self.prod_a.is_deleted)

    def test_buyer_company_cannot_access_seller_products(self):
        """Buyer-only company is blocked from Seller product management"""
        self.client.login(email="admin@buyeronly.com", password="Password@123")

        # Web view redirects to dashboard
        web_resp = self.client.get(reverse("products-list"))
        self.assertEqual(web_resp.status_code, 302)

        # REST API returns 403 Forbidden
        api_resp = self.client.get(reverse("api-products-list"))
        self.assertEqual(api_resp.status_code, 403)


class RBACDedicatedPageTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Company Admin
        self.admin_user = User.objects.create_user(
            email="admin@matrixcorp.com",
            password="Password@123",
            first_name="Anita",
            last_name="Deshmukh",
        )
        self.company = Company.objects.create(
            name="Matrix Corp Pvt. Ltd.",
            company_type=Company.CompanyType.BOTH,
            created_by=self.admin_user,
        )
        self.admin_member = CompanyMember.objects.create(
            user=self.admin_user,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 2. Regular Company User
        self.staff_user = User.objects.create_user(
            email="staff@matrixcorp.com",
            password="Password@123",
            first_name="Vikram",
            last_name="Singh",
        )
        self.staff_member = CompanyMember.objects.create(
            user=self.staff_user,
            company=self.company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )

        # 3. Super Admin
        self.superadmin = User.objects.create_superuser(
            email="super@platform.com",
            password="Password@123",
            first_name="Super",
            last_name="Admin",
        )

    def test_company_admin_can_access_rbac_page(self):
        """Company Admin can view dedicated RBAC page with 6 action permissions and matrix"""
        self.client.login(email="admin@matrixcorp.com", password="Password@123")
        resp = self.client.get(reverse("company-rbac-web"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Role-Based Access Control (RBAC)")
        self.assertContains(resp, "READ")
        self.assertContains(resp, "EDIT")
        self.assertContains(resp, "UPDATE")
        self.assertContains(resp, "DELETE")
        self.assertContains(resp, "IMPORT")
        self.assertContains(resp, "EXPORT")
        self.assertContains(resp, "Matrix Corp Pvt. Ltd.")
        self.assertContains(resp, "Vikram Singh")

    def test_company_user_restricted_from_rbac_page(self):
        """Regular Company User cannot access RBAC page and is redirected"""
        self.client.login(email="staff@matrixcorp.com", password="Password@123")
        resp = self.client.get(reverse("company-rbac-web"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard", resp.url)

    def test_admin_can_save_6_permissions_for_user(self):
        """Company Admin configures checkboxes for actions on RBAC page"""
        self.client.login(email="admin@matrixcorp.com", password="Password@123")

        # Grant READ, EDIT, and EXPORT
        resp = self.client.post(reverse("company-rbac-web"), {
            "action": "save_permissions",
            "member_id": self.staff_member.id,
            "permissions": ["READ", "EDIT", "EXPORT"],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/company/rbac/", resp.url)

        # Verify permissions in database
        perms = set(MemberPermission.objects.filter(member=self.staff_member).values_list("permission__permission", flat=True))
        self.assertEqual(perms, {"READ", "EDIT", "EXPORT"})

        # Grant all 6 permissions
        resp_all = self.client.post(reverse("company-rbac-web"), {
            "action": "save_permissions",
            "member_id": self.staff_member.id,
            "permissions": ["READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"],
        })
        self.assertEqual(resp_all.status_code, 302)
        all_perms = set(MemberPermission.objects.filter(member=self.staff_member).values_list("permission__permission", flat=True))
        self.assertEqual(all_perms, {"READ", "EDIT", "UPDATE", "DELETE", "IMPORT", "EXPORT"})

    def test_admin_permissions_locked_in_rbac(self):
        """Admin permissions cannot be modified or restricted via RBAC page"""
        self.client.login(email="admin@matrixcorp.com", password="Password@123")

        resp = self.client.post(reverse("company-rbac-web"), {
            "action": "save_permissions",
            "member_id": self.admin_member.id,
            "permissions": ["READ"],
        })
        self.assertEqual(resp.status_code, 302)

    def test_ajax_permission_save_returns_json(self):
        """AJAX request to save permissions returns JSON response"""
        self.client.login(email="admin@matrixcorp.com", password="Password@123")

        resp = self.client.post(
            reverse("company-rbac-web"),
            {
                "action": "save_permissions",
                "member_id": self.staff_member.id,
                "permissions": ["READ", "UPDATE"],
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["permissions"], ["READ", "UPDATE"])

    def test_rbac_buyer_and_seller_company_modules_scope(self):
        """RBAC correctly scopes modules for Buyer-only and Seller-only companies"""
        # 1. Buyer Company
        buyer_admin = User.objects.create_user(email="buyeradmin@buyercomp.com", password="Password@123")
        buyer_comp = Company.objects.create(name="Buyer Comp Ltd", company_type=Company.CompanyType.BUYER, created_by=buyer_admin)
        CompanyMember.objects.create(user=buyer_admin, company=buyer_comp, role=CompanyMember.Role.ADMIN, is_active=True)
        buyer_user = User.objects.create_user(email="user@buyercomp.com", password="Password@123")
        buyer_member = CompanyMember.objects.create(user=buyer_user, company=buyer_comp, role=CompanyMember.Role.USER, is_active=True)

        self.client.login(email="buyeradmin@buyercomp.com", password="Password@123")
        # Save READ + EDIT
        self.client.post(reverse("company-rbac-web"), {
            "action": "save_permissions",
            "member_id": buyer_member.id,
            "permissions": ["READ", "EDIT"],
        })
        # Check modules assigned: should include requirements and general, NOT products/catalog
        assigned_modules = set(MemberPermission.objects.filter(member=buyer_member).values_list("permission__module", flat=True))
        self.assertIn("requirements", assigned_modules)
        self.assertNotIn("products", assigned_modules)


class BuyerModuleTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Company Alpha (Buyer)
        self.alpha_admin = User.objects.create_user(
            email="admin@alphaprocure.com",
            password="Password@123",
            first_name="Alok",
            last_name="Nath",
        )
        self.alpha_company = Company.objects.create(
            name="Alpha Procurements Pvt. Ltd.",
            company_type=Company.CompanyType.BUYER,
            created_by=self.alpha_admin,
            city="Delhi",
        )
        CompanyMember.objects.create(
            user=self.alpha_admin,
            company=self.alpha_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Alpha Reader (User with only READ permission)
        self.alpha_reader = User.objects.create_user(
            email="reader@alphaprocure.com",
            password="Password@123",
            first_name="Rohan",
            last_name="Reader",
        )
        self.alpha_reader_member = CompanyMember.objects.create(
            user=self.alpha_reader,
            company=self.alpha_company,
            role=CompanyMember.Role.USER,
            is_active=True,
        )
        perm_read, _ = CompanyPermission.objects.get_or_create(module="requirements", permission="READ")
        MemberPermission.objects.create(member=self.alpha_reader_member, permission=perm_read)

        # 2. Company Beta (Buyer) - For Data Isolation Test
        self.beta_admin = User.objects.create_user(
            email="admin@betaprocure.com",
            password="Password@123",
            first_name="Bhavesh",
            last_name="Shah",
        )
        self.beta_company = Company.objects.create(
            name="Beta Procurements Pvt. Ltd.",
            company_type=Company.CompanyType.BUYER,
            created_by=self.beta_admin,
            city="Surat",
        )
        CompanyMember.objects.create(
            user=self.beta_admin,
            company=self.beta_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # 3. Seller-Only Company (Should be blocked from Buyer module)
        self.seller_admin = User.objects.create_user(
            email="admin@gammasupply.com",
            password="Password@123",
            first_name="Girish",
            last_name="Patel",
        )
        self.seller_company = Company.objects.create(
            name="Gamma Supply Pvt. Ltd.",
            company_type=Company.CompanyType.SELLER,
            created_by=self.seller_admin,
        )
        CompanyMember.objects.create(
            user=self.seller_admin,
            company=self.seller_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Category
        self.category = Category.objects.create(name="Heavy Machinery")

        # Sample Alpha Requirement
        self.req_alpha = Requirement.objects.create(
            company=self.alpha_company,
            created_by=self.alpha_admin,
            item_name="Hydraulic Excavator 20 Ton",
            category=self.category,
            quantity=2,
            unit="units",
            target_price=3500000.00,
            currency="INR",
            delivery_city="Delhi",
            delivery_country="India",
            specifications="Operating weight 20,000 kg, bucket capacity 0.9 m3",
            search_scope=Requirement.SearchScope.GLOBAL,
            status=Requirement.Status.SEARCHING,
            is_deleted=False,
        )

    def test_buyer_requirements_crud_web_view(self):
        """Complete Requirement Web CRUD: List, Create, Detail, Edit, Soft Delete"""
        self.client.login(email="admin@alphaprocure.com", password="Password@123")

        # 1. LIST Web View
        list_resp = self.client.get(reverse("requirements-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, "Buyer Requirements")
        self.assertContains(list_resp, "Hydraulic Excavator 20 Ton")
        self.assertContains(list_resp, "Alpha Procurements Pvt. Ltd.")

        # 2. CREATE Web View
        create_resp = self.client.post(reverse("requirements-create"), {
            "item_name": "Diesel Generator 250 kVA",
            "category_id": self.category.id,
            "quantity": "5",
            "unit": "pcs",
            "target_price": "850000.00",
            "currency": "INR",
            "delivery_city": "Noida",
            "delivery_country": "India",
            "search_scope": "country",
            "specifications": "Caterpillar or Cummins engine, 3-phase, silent canopy",
            "status": "searching",
        })
        self.assertEqual(create_resp.status_code, 302)
        new_req = Requirement.objects.get(item_name="Diesel Generator 250 kVA")
        self.assertEqual(new_req.company, self.alpha_company)
        self.assertEqual(float(new_req.target_price), 850000.00)
        self.assertFalse(new_req.is_deleted)

        # 3. DETAIL Web View
        detail_resp = self.client.get(reverse("requirement-detail", kwargs={"pk": new_req.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Diesel Generator 250 kVA")
        self.assertContains(detail_resp, "Procurement Parameters")
        self.assertContains(detail_resp, "Technical Specifications")

        # 4. EDIT Web View
        edit_resp = self.client.post(reverse("requirement-edit", kwargs={"pk": new_req.id}), {
            "item_name": "Diesel Generator 250 kVA (Silent)",
            "category_id": self.category.id,
            "quantity": "6",
            "unit": "pcs",
            "target_price": "900000.00",
            "currency": "INR",
            "delivery_city": "Gurugram",
            "delivery_country": "India",
            "search_scope": "country",
            "specifications": "Updated specs: CPCB-IV+ emission compliant",
            "status": "searching",
        })
        self.assertEqual(edit_resp.status_code, 302)
        new_req.refresh_from_db()
        self.assertEqual(new_req.item_name, "Diesel Generator 250 kVA (Silent)")
        self.assertEqual(new_req.quantity, 6)
        self.assertEqual(float(new_req.target_price), 900000.00)

        # 5. SOFT DELETE Web View
        del_resp = self.client.post(reverse("requirement-delete", kwargs={"pk": new_req.id}))
        self.assertEqual(del_resp.status_code, 302)
        new_req.refresh_from_db()
        self.assertTrue(new_req.is_deleted)
        self.assertIsNotNone(new_req.deleted_at)

        # Soft-deleted item is no longer in list view
        list_after = self.client.get(reverse("requirements-list"))
        self.assertNotIn(new_req, list_after.context["requirements"])
        self.assertNotContains(list_after, f"/procurement/requirements/{new_req.id}/")

        # Detail view returns 404 for soft-deleted item
        del_detail = self.client.get(reverse("requirement-detail", kwargs={"pk": new_req.id}))
        self.assertEqual(del_detail.status_code, 404)

    def test_buyer_requirements_crud_api(self):
        """Complete Requirement REST API CRUD: List, Create, Detail, Update, Soft-Delete"""
        self.client.login(email="admin@alphaprocure.com", password="Password@123")

        # 1. LIST API
        list_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()), 1)
        self.assertEqual(list_resp.json()[0]["item_name"], "Hydraulic Excavator 20 Ton")

        # 2. CREATE API
        create_resp = self.client.post(
            reverse("api-requirements-list"),
            {
                "item_name": "Stainless Steel Pipes Grade 316",
                "quantity": 100,
                "unit": "meters",
                "target_price": "1200.00",
                "delivery_city": "Faridabad",
                "search_scope": "global",
            },
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 201)
        new_id = create_resp.json()["id"]

        # 3. DETAIL API
        detail_resp = self.client.get(reverse("api-requirements-detail", kwargs={"pk": new_id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["item_name"], "Stainless Steel Pipes Grade 316")

        # 4. PATCH API
        patch_resp = self.client.patch(
            reverse("api-requirements-detail", kwargs={"pk": new_id}),
            {"target_price": "1350.00"},
            content_type="application/json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(float(patch_resp.json()["target_price"]), 1350.00)

        # 5. DELETE API (soft-delete)
        del_resp = self.client.delete(reverse("api-requirements-detail", kwargs={"pk": new_id}))
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.json()["is_deleted"])

        del_req = Requirement.objects.get(id=new_id)
        self.assertTrue(del_req.is_deleted)

        # Detail returns 404 after soft delete
        get_after = self.client.get(reverse("api-requirements-detail", kwargs={"pk": new_id}))
        self.assertEqual(get_after.status_code, 404)

    def test_buyer_rbac_permission_checks(self):
        """Company User with only READ permission can view but cannot create, edit, or delete"""
        self.client.login(email="reader@alphaprocure.com", password="Password@123")

        # Can view list and detail
        self.assertEqual(self.client.get(reverse("requirements-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("requirement-detail", kwargs={"pk": self.req_alpha.id})).status_code, 200)

        # Cannot create
        create_resp = self.client.post(reverse("requirements-create"), {"item_name": "Blocked Item"})
        self.assertEqual(create_resp.status_code, 302)

        # Cannot edit
        edit_resp = self.client.post(reverse("requirement-edit", kwargs={"pk": self.req_alpha.id}), {"item_name": "Blocked Edit"})
        self.assertEqual(edit_resp.status_code, 302)

        # Cannot delete
        del_resp = self.client.post(reverse("requirement-delete", kwargs={"pk": self.req_alpha.id}))
        self.assertEqual(del_resp.status_code, 302)
        self.req_alpha.refresh_from_db()
        self.assertFalse(self.req_alpha.is_deleted)

    def test_buyer_multi_tenant_data_isolation(self):
        """Alpha Company cannot see Beta Company requirements and vice versa"""
        # Beta admin creates Beta requirement
        req_beta = Requirement.objects.create(
            company=self.beta_company,
            created_by=self.beta_admin,
            item_name="Beta Special Textile Loom",
            quantity=1,
            unit="set",
            is_deleted=False,
        )

        # Alpha admin visits
        self.client.login(email="admin@alphaprocure.com", password="Password@123")
        alpha_list = self.client.get(reverse("requirements-list"))
        self.assertContains(alpha_list, "Hydraulic Excavator 20 Ton")
        self.assertNotContains(alpha_list, "Beta Special Textile Loom")

        # Alpha admin cannot view Beta detail
        alpha_beta_detail = self.client.get(reverse("requirement-detail", kwargs={"pk": req_beta.id}))
        self.assertEqual(alpha_beta_detail.status_code, 404)

        # Beta admin visits
        self.client.login(email="admin@betaprocure.com", password="Password@123")
        beta_list = self.client.get(reverse("requirements-list"))
        self.assertContains(beta_list, "Beta Special Textile Loom")
        self.assertNotContains(beta_list, "Hydraulic Excavator 20 Ton")

    def test_seller_only_company_blocked_from_requirements(self):
        """Seller-only company is blocked from Buyer requirement management"""
        self.client.login(email="admin@gammasupply.com", password="Password@123")

        # Web view redirects to dashboard
        web_resp = self.client.get(reverse("requirements-list"))
        self.assertEqual(web_resp.status_code, 302)

        # REST API returns 403 Forbidden
        api_resp = self.client.get(reverse("api-requirements-list"))
        self.assertEqual(api_resp.status_code, 403)









