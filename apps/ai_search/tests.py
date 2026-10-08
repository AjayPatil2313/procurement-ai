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
