import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.companies.models import Company, CompanyMember
from apps.ai_search.models import MatchingParameter, ensure_default_parameters_for_company
from apps.ai_search.services.ai_matcher import AIMatcher

User = get_user_model()


class DynamicMatchingParametersTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="seller@apexind.com",
            password="testpassword123",
            first_name="Raj",
            last_name="Verma",
        )
        self.company = Company.objects.create(
            name="Apex Heavy Industries Pvt Ltd",
            company_type=Company.CompanyType.SELLER,
            industry="Machinery & Engineering",
            city="Pune",
            country="India",
            created_by=self.user,
        )
        self.member = CompanyMember.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.client = Client()
        self.client.login(email="seller@apexind.com", password="testpassword123")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_default_parameters_creation(self):
        """Verify that default 10 B2B parameters are initialized with exactly 100% total weight."""
        params = ensure_default_parameters_for_company(self.company)
        self.assertEqual(len(params), 10)

        total_weight = sum(p.weight_percentage for p in params if p.is_active)
        self.assertEqual(total_weight, 100)

        param_keys = [p.parameter_key for p in params]
        self.assertIn("entity_type", param_keys)
        self.assertIn("verified_status", param_keys)
        self.assertIn("location", param_keys)
        self.assertIn("product_matching", param_keys)
        self.assertIn("categories_match", param_keys)
        self.assertIn("buyer_req_match", param_keys)
        self.assertIn("industry_match", param_keys)
        self.assertIn("specifications", param_keys)
        self.assertIn("profile_relevance", param_keys)
        self.assertIn("b2b_model", param_keys)

    def test_ai_matcher_heuristic_evaluation_with_parameters(self):
        """Verify heuristic AI scoring incorporates active parameter criteria."""
        params = ensure_default_parameters_for_company(self.company)

        candidate = {
            "name": "Tata Motors Engineering Pvt Ltd",
            "description": "Leading commercial vehicle and industrial machinery buyer looking for CNC units and transmission parts.",
            "phone": "+91 20 6656 1234",
            "email": "procurement@tatamotors.com",
            "city": "Pune",
            "country": "India",
            "industry": "Automotive & Machinery",
            "company_role": "buyer",
        }

        matcher = AIMatcher()
        result = matcher._evaluate_heuristic(
            scraped_company=candidate,
            target_item_name="CNC Precision Milling Spindle",
            target_specs="High-grade CNC spindle for automotive machinery",
            matching_parameters=params,
        )

        self.assertGreaterEqual(result["match_score"], 70)
        self.assertIn("matched_parameters", result)
        matched_tags = result["matched_parameters"]
        # Candidate is Pvt Ltd and has phone/email in Pune
        self.assertTrue(any("Pvt Ltd" in tag for tag in matched_tags))
        self.assertTrue(any("Verified" in tag for tag in matched_tags))
        self.assertTrue(any("Location" in tag for tag in matched_tags))

    def test_matching_parameters_view_listing(self):
        """Verify web view displays parameters and initializes defaults if missing."""
        response = self.client.get(reverse("matching-parameters"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Company Matching Parameters")
        self.assertContains(response, "Legal Entity Structure (Pvt Ltd / Ltd)")
        self.assertContains(response, "Verified Contact")

    def test_create_custom_matching_parameter(self):
        """Verify creating a custom matching parameter."""
        response = self.client.post(
            reverse("matching-parameter-create"),
            {
                "name": "ISO 9001 Certified Supplier Requirement",
                "criteria_value": "ISO 9001, Quality Certified, CE Certified",
                "rule_type": "weighted",
                "weight_percentage": "15",
                "description": "Ensure buyer or partner holds quality accreditations",
                "is_active": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        param = MatchingParameter.objects.filter(
            company=self.company,
            name="ISO 9001 Certified Supplier Requirement"
        ).first()
        self.assertIsNotNone(param)
        self.assertEqual(param.weight_percentage, 15)
        self.assertEqual(param.rule_type, MatchingParameter.RuleType.WEIGHTED)
        self.assertTrue(param.is_active)

    def test_toggle_matching_parameter_ajax(self):
        """Verify AJAX toggle switches parameter active state."""
        param = MatchingParameter.objects.create(
            company=self.company,
            name="Export Turnover > $5M",
            parameter_key="export_turnover",
            criteria_value="Export Turnover",
            rule_type=MatchingParameter.RuleType.BONUS,
            weight_percentage=10,
            is_active=True,
        )

        response = self.client.post(
            reverse("matching-parameter-toggle", args=[param.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["is_active"])

        param.refresh_from_db()
        self.assertFalse(param.is_active)

    def test_edit_matching_parameter(self):
        """Verify editing an existing parameter."""
        param = MatchingParameter.objects.create(
            company=self.company,
            name="Raw Material Capacity",
            parameter_key="raw_material_capacity",
            criteria_value="Capacity 100T",
            rule_type=MatchingParameter.RuleType.WEIGHTED,
            weight_percentage=10,
            is_active=True,
        )

        response = self.client.post(
            reverse("matching-parameter-edit", args=[param.id]),
            {
                "name": "Updated Material Capacity Gate",
                "criteria_value": "Capacity 500T",
                "rule_type": "mandatory",
                "weight_percentage": "25",
                "description": "Updated strict gate",
                "is_active": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        param.refresh_from_db()
        self.assertEqual(param.name, "Updated Material Capacity Gate")
        self.assertEqual(param.rule_type, MatchingParameter.RuleType.MANDATORY)
        self.assertEqual(param.weight_percentage, 25)

    def test_delete_matching_parameter(self):
        """Verify deleting a parameter."""
        param = MatchingParameter.objects.create(
            company=self.company,
            name="Temporary Rule",
            parameter_key="temporary_rule",
            criteria_value="test",
            rule_type=MatchingParameter.RuleType.WEIGHTED,
            weight_percentage=5,
            is_active=True,
        )

        response = self.client.post(
            reverse("matching-parameter-delete", args=[param.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MatchingParameter.objects.filter(id=param.id).exists())

    def test_reset_defaults_matching_parameters(self):
        """Verify resetting defaults replaces all company rules with the 10 standard criteria."""
        MatchingParameter.objects.create(
            company=self.company,
            name="Old Custom Junk Rule",
            parameter_key="junk",
            is_active=True,
        )

        response = self.client.post(reverse("matching-parameter-reset"))
        self.assertEqual(response.status_code, 302)

        params = MatchingParameter.objects.filter(company=self.company)
        self.assertEqual(params.count(), 10)
        self.assertFalse(params.filter(parameter_key="junk").exists())

    def test_web_search_provider_serpapi_and_google_cse_adapters(self):
        """Verify WebSearchProvider handles SerpAPI, Google CSE, and fallback candidates safely."""
        from unittest.mock import patch, MagicMock
        from apps.ai_search.services.search_provider import WebSearchProvider

        provider = WebSearchProvider(timeout=1.0)

        # 1. Test SerpAPI with mocked response
        with patch.dict("os.environ", {"SERPAPI_API_KEY": "test_serp_key"}):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "organic_results": [
                    {
                        "title": "Tata Steel Industrial Procurement",
                        "link": "https://www.tatasteel.com/procurement",
                        "snippet": "Official procurement portal for industrial buyers and vendors.",
                    }
                ]
            }
            with patch("requests.get", return_value=mock_resp):
                results = provider.search("steel procurement india", num_results=1)
                self.assertEqual(len(results), 1)
                self.assertIn("tatasteel.com", results[0]["url"])

        # 2. Test Fallback candidates when no APIs configured
        with patch.dict("os.environ", {"SERPAPI_API_KEY": "", "GOOGLE_SEARCH_API_KEY": "", "GEMINI_API_KEY": ""}):
            results_fallback = provider.search("industrial valves manufacturers", num_results=2)
            self.assertGreaterEqual(len(results_fallback), 1)
            self.assertTrue(results_fallback[0]["url"].startswith("http"))


class PlatformMatchingCriteriaTestCase(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="superadmin@platform.com",
            password="adminpassword123",
            first_name="Super",
            last_name="Admin",
        )
        self.seller_user = User.objects.create_user(
            email="seller@example.com",
            password="sellerpass123",
            first_name="Seller",
            last_name="User",
        )
        self.seller_company = Company.objects.create(
            name="Apex Engineering Ltd",
            company_type=Company.CompanyType.SELLER,
            industry="Engineering",
            city="Mumbai",
            created_by=self.seller_user,
        )
        CompanyMember.objects.create(
            user=self.seller_user,
            company=self.seller_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.buyer_user = User.objects.create_user(
            email="buyer@example.com",
            password="buyerpass123",
            first_name="Buyer",
            last_name="User",
        )
        self.buyer_company = Company.objects.create(
            name="Zenith Procurement Corp",
            company_type=Company.CompanyType.BUYER,
            industry="Procurement",
            city="Delhi",
            created_by=self.buyer_user,
        )
        CompanyMember.objects.create(
            user=self.buyer_user,
            company=self.buyer_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.admin_client = Client()
        self.admin_client.login(email="superadmin@platform.com", password="adminpassword123")

        self.seller_client = Client()
        self.seller_client.login(email="seller@example.com", password="sellerpass123")
        seller_session = self.seller_client.session
        seller_session["active_company_id"] = self.seller_company.id
        seller_session.save()

        self.buyer_client = Client()
        self.buyer_client.login(email="buyer@example.com", password="buyerpass123")
        buyer_session = self.buyer_client.session
        buyer_session["active_company_id"] = self.buyer_company.id
        buyer_session.save()

    def test_platform_criteria_seeding_and_domain_queries(self):
        """Test default seeding and domain-specific filtering for SALES, VENDOR, and BOTH."""
        from apps.ai_search.models import PlatformMatchingCriteria
        PlatformMatchingCriteria.objects.all().delete()
        self.assertEqual(PlatformMatchingCriteria.objects.count(), 0)

        # Seeding
        count = PlatformMatchingCriteria.seed_default_criteria()
        self.assertEqual(count, 10)
        self.assertEqual(PlatformMatchingCriteria.objects.count(), 10)

        # Domain queries
        both_criteria = list(PlatformMatchingCriteria.get_criteria_for_domain("BOTH"))
        self.assertEqual(len(both_criteria), 10)

        # Add a domain-specific criterion for SALES only
        sales_only = PlatformMatchingCriteria.objects.create(
            name="ISO 14001 Environmental Certification",
            parameter_key="iso_14001",
            target_domain=PlatformMatchingCriteria.TargetDomain.SALES,
            default_value="ISO 14001:2015 Accredited",
            rule_type=PlatformMatchingCriteria.RuleType.MANDATORY,
            weight_percentage=15,
            icon="fa-leaf",
            color="emerald",
            order=15,
            is_active=True,
        )

        # Add a domain-specific criterion for VENDOR only
        vendor_only = PlatformMatchingCriteria.objects.create(
            name="Immediate Credit Line Approved",
            parameter_key="credit_line",
            target_domain=PlatformMatchingCriteria.TargetDomain.VENDOR,
            default_value="30-day corporate credit limit",
            rule_type=PlatformMatchingCriteria.RuleType.BONUS,
            weight_percentage=10,
            icon="fa-credit-card",
            color="blue",
            order=25,
            is_active=True,
        )

        sales_list = list(PlatformMatchingCriteria.get_criteria_for_domain("SALES"))
        sales_keys = [c.parameter_key for c in sales_list]
        self.assertIn("iso_14001", sales_keys)
        self.assertNotIn("credit_line", sales_keys)

        vendor_list = list(PlatformMatchingCriteria.get_criteria_for_domain("VENDOR"))
        vendor_keys = [c.parameter_key for c in vendor_list]
        self.assertIn("credit_line", vendor_keys)
        self.assertNotIn("iso_14001", vendor_keys)

        # Inactive criteria should be excluded
        sales_only.is_active = False
        sales_only.save()
        sales_list_after = list(PlatformMatchingCriteria.get_criteria_for_domain("SALES"))
        self.assertNotIn("iso_14001", [c.parameter_key for c in sales_list_after])

    def test_super_admin_matching_criteria_view_permissions_and_listing(self):
        """Test Super Admin matching criteria page access control and default 10 pagination."""
        # Non-superadmin access blocked
        resp_seller = self.seller_client.get(reverse("admin-panel-matching-criteria"))
        self.assertEqual(resp_seller.status_code, 403)

        # Superadmin access success
        resp_admin = self.admin_client.get(reverse("admin-panel-matching-criteria"))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn("page_obj", resp_admin.context)
        self.assertEqual(resp_admin.context["page_obj"].paginator.per_page, 10)

    def test_super_admin_crud_actions(self):
        """Test Super Admin create, edit, toggle, and delete actions for matching criteria."""
        from apps.ai_search.models import PlatformMatchingCriteria

        # 1. Create action
        create_data = {
            "action": "create",
            "name": "ESG Carbon Neutrality Standard",
            "parameter_key": "esg_carbon_neutral",
            "target_domain": "SALES",
            "rule_type": "weighted",
            "weight_percentage": "12",
            "icon": "fa-seedling",
            "color": "teal",
            "order": "5",
            "default_value": "Scope 1 & 2 Emissions Reported",
            "description": "Enterprise carbon audit report validated.",
            "is_active": "1",
        }
        resp = self.admin_client.post(reverse("admin-panel-matching-criteria"), data=create_data)
        self.assertEqual(resp.status_code, 302)

        crit = PlatformMatchingCriteria.objects.filter(parameter_key="esg_carbon_neutral").first()
        self.assertIsNotNone(crit)
        self.assertEqual(crit.name, "ESG Carbon Neutrality Standard")
        self.assertEqual(crit.target_domain, "SALES")
        self.assertEqual(crit.weight_percentage, 12)
        self.assertTrue(crit.is_active)

        # 2. Edit action
        edit_data = {
            "action": "edit",
            "criteria_id": crit.id,
            "name": "ESG & Net-Zero Carbon Commitment",
            "target_domain": "BOTH",
            "rule_type": "mandatory",
            "weight_percentage": "20",
            "icon": "fa-seedling",
            "color": "emerald",
            "order": "3",
            "default_value": "Scope 1, 2 & 3 Emissions Reported",
            "description": "Full net-zero decarbonization compliance.",
            "is_active": "1",
        }
        resp = self.admin_client.post(reverse("admin-panel-matching-criteria"), data=edit_data)
        self.assertEqual(resp.status_code, 302)

        crit.refresh_from_db()
        self.assertEqual(crit.name, "ESG & Net-Zero Carbon Commitment")
        self.assertEqual(crit.target_domain, "BOTH")
        self.assertEqual(crit.weight_percentage, 20)
        self.assertEqual(crit.rule_type, "mandatory")

        # 3. Toggle action
        resp = self.admin_client.post(reverse("admin-panel-matching-criteria"), data={
            "action": "toggle",
            "criteria_id": crit.id,
        })
        self.assertEqual(resp.status_code, 302)
        crit.refresh_from_db()
        self.assertFalse(crit.is_active)

        # Toggle back
        self.admin_client.post(reverse("admin-panel-matching-criteria"), data={
            "action": "toggle",
            "criteria_id": crit.id,
        })
        crit.refresh_from_db()
        self.assertTrue(crit.is_active)

        # 4. Delete action
        resp = self.admin_client.post(reverse("admin-panel-matching-criteria"), data={
            "action": "delete",
            "criteria_id": crit.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PlatformMatchingCriteria.objects.filter(parameter_key="esg_carbon_neutral", is_active=True).exists())

    def test_dynamic_criteria_displayed_in_find_buyers_and_suppliers(self):
        """Test that newly added Super Admin criteria immediately appear dynamically in Find Buyers & Find Suppliers."""
        from apps.ai_search.models import PlatformMatchingCriteria

        # Create a Sales-only criterion in Super Admin
        sales_crit = PlatformMatchingCriteria.objects.create(
            name="Defense Empaneled Vendor Status",
            parameter_key="defense_empaneled",
            target_domain=PlatformMatchingCriteria.TargetDomain.SALES,
            default_value="DGQA / DRDO Empanelment Registered",
            is_active=True,
            order=1,
        )

        # Create a Vendor-only criterion in Super Admin
        vendor_crit = PlatformMatchingCriteria.objects.create(
            name="Direct EPC Project Contractor",
            parameter_key="epc_contractor",
            target_domain=PlatformMatchingCriteria.TargetDomain.VENDOR,
            default_value="Tier-1 EPC Contractor with active capex projects",
            is_active=True,
            order=2,
        )

        # 1. Check Sales (Find Suppliers)
        resp_sales = self.buyer_client.get(reverse("find-suppliers"))
        self.assertEqual(resp_sales.status_code, 200)
        static_keys_sales = [c["key"] for c in resp_sales.context["static_criteria_list"]]
        self.assertIn("defense_empaneled", static_keys_sales)
        self.assertNotIn("epc_contractor", static_keys_sales)

        # 2. Check Vendor (Find Buyers)
        resp_vendor = self.seller_client.get(reverse("find-buyers"))
        self.assertEqual(resp_vendor.status_code, 200)
        static_keys_vendor = [c["key"] for c in resp_vendor.context["static_criteria_list"]]
        self.assertIn("epc_contractor", static_keys_vendor)
        self.assertNotIn("defense_empaneled", static_keys_vendor)


class AIFailoverGatewayTestCase(TestCase):
    """
    Unit tests for Tier 1 Cognitive AI Generation & Cloud Failover Architecture.
    Validates Gemini 2.5 Flash primary engine, automated OpenAI gpt-4o-mini failover on 429/503/timeout,
    telemetry callbacks, and Super Admin live simulator.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="superadmin_failover@platform.ai",
            password="SuperPassword123!",
            first_name="Super",
            last_name="Admin",
        )
        self.client = Client()

    def test_primary_gemini_200_ok(self):
        """When Gemini returns 200 OK, candidate stream is emitted directly without failover."""
        from unittest.mock import patch
        from apps.ai_search.services.ai_gateway import AIFailoverGateway

        mock_gemini_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps({"match_score": 92, "recommendation": "Prime Supplier"})}
                        ]
                    }
                }
            ]
        }

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_gemini_resp

            with patch.object(AIFailoverGateway, "get_gemini_key", return_value="mock-gemini-key"):
                res, telemetry = AIFailoverGateway.generate("Evaluate supplier fit")
                self.assertIsNotNone(res)
                self.assertEqual(res.get("match_score"), 92)
                self.assertFalse(telemetry["failover_occurred"])
                self.assertEqual(telemetry["engine_used"], "Google Gemini 2.5 Flash")
                self.assertEqual(telemetry["primary_status_code"], 200)

    def test_gemini_429_trips_to_openai_failover(self):
        """HTTP 429 Rate Limit from Gemini triggers Cloud Failover to OpenAI gpt-4o-mini Backup."""
        from unittest.mock import patch, MagicMock
        from apps.ai_search.services.ai_gateway import AIFailoverGateway

        callback_events = []

        def sample_callback(event):
            callback_events.append(event)

        def mock_post_dispatcher(url, *args, **kwargs):
            mock = MagicMock()
            if "generativelanguage.googleapis.com" in url:
                # Gemini hits 429 rate limit
                mock.status_code = 429
                mock.json.return_value = {"error": "Rate limit exceeded"}
            elif "api.openai.com" in url:
                # OpenAI Backup responds 200 OK
                mock.status_code = 200
                mock.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"status": "ok", "failover_verified": True})
                            }
                        }
                    ]
                }
            return mock

        with patch("requests.post", side_effect=mock_post_dispatcher):
            with patch.object(AIFailoverGateway, "get_gemini_key", return_value="mock-gemini-key"):
                with patch.object(AIFailoverGateway, "get_openai_key", return_value="mock-openai-key"):
                    res, telemetry = AIFailoverGateway.generate(
                        prompt="Generate candidate naming stream",
                        callback=sample_callback,
                    )
                    self.assertIsNotNone(res)
                    self.assertTrue(res.get("failover_verified"))
                    self.assertTrue(telemetry["failover_occurred"])
                    self.assertEqual(telemetry["primary_status_code"], 429)
                    self.assertEqual(telemetry["failover_status_code"], 200)
                    self.assertEqual(telemetry["engine_used"], "OpenAI gpt-4o-mini Backup")
                    self.assertEqual(len(callback_events), 1)
                    self.assertEqual(callback_events[0]["primary_status_code"], 429)

    def test_gemini_503_and_timeout_failover(self):
        """HTTP 503 or Request Timeout on Gemini automatically trips to OpenAI backup."""
        from unittest.mock import patch, MagicMock
        import requests
        from apps.ai_search.services.ai_gateway import AIFailoverGateway

        def mock_timeout_dispatcher(url, *args, **kwargs):
            if "generativelanguage.googleapis.com" in url:
                raise requests.exceptions.Timeout("Gemini connection timed out")
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {
                "choices": [{"message": {"content": json.dumps({"recovered": True})}}]
            }
            return mock

        with patch("requests.post", side_effect=mock_timeout_dispatcher):
            with patch.object(AIFailoverGateway, "get_gemini_key", return_value="mock-gemini-key"):
                with patch.object(AIFailoverGateway, "get_openai_key", return_value="mock-openai-key"):
                    res, telemetry = AIFailoverGateway.generate("Heavy B2B search")
                    self.assertIsNotNone(res)
                    self.assertTrue(res.get("recovered"))
                    self.assertTrue(telemetry["failover_occurred"])
                    self.assertEqual(telemetry["primary_status_code"], 408)
                    self.assertEqual(telemetry["engine_used"], "OpenAI gpt-4o-mini Backup")

    def test_admin_panel_ai_providers_view_and_simulation(self):
        """Super Admin can access AI Providers dashboard and run Live Failover Simulation."""
        self.client.login(email="superadmin_failover@platform.ai", password="SuperPassword123!")

        # 1. GET Dashboard
        get_res = self.client.get(reverse("admin-panel-ai-providers"))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, "Cognitive AI Engines &amp; Cloud Failover")
        self.assertContains(get_res, "Google Gemini 2.5 Flash")
        self.assertContains(get_res, "OpenAI gpt-4o-mini")
        self.assertContains(get_res, "Live AI Naming &amp; Failover Simulator")

        # 2. POST Simulation with simulated 429
        from unittest.mock import patch
        from apps.ai_search.services.ai_gateway import AIFailoverGateway

        sim_telemetry = {
            "success": True,
            "engine_used": "OpenAI gpt-4o-mini Backup",
            "provider_code": "OPENAI",
            "failover_occurred": True,
            "failover_trigger": "Primary Google Gemini 2.5 Flash failed [Simulated HTTP 429]",
            "primary_status_code": 429,
            "failover_status_code": 200,
            "primary_latency_ms": 310,
            "failover_latency_ms": 380,
            "total_latency_ms": 690,
            "error": None,
        }

        with patch.object(AIFailoverGateway, "generate", return_value=({"candidates": [{"title": "Precision Parts Corp"}]}, sim_telemetry)):
            post_res = self.client.post(
                reverse("admin-panel-ai-providers"),
                {
                    "action": "simulate_failover",
                    "prompt": "Find CNC manufacturers in Pune",
                    "simulation_mode": "429",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(post_res.status_code, 200)
            data = post_res.json()
            self.assertTrue(data["success"])
            self.assertTrue(data["failover_occurred"])
            self.assertEqual(data["engine_used"], "OpenAI gpt-4o-mini Backup")
            self.assertEqual(data["primary_status_code"], 429)
