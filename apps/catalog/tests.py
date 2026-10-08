from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.companies.models import Company, CompanyMember
from apps.catalog.models import Product, Category
from apps.ai_search.models import MatchingParameter

User = get_user_model()


class FindVendorsCriteriaTestCase(TestCase):
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
        self.category = Category.objects.create(name="Industrial Valves")
        self.product = Product.objects.create(
            company=self.company,
            name="High Pressure Ball Valve",
            category=self.category,
            created_by=self.user,
        )

        self.client = Client()
        self.client.login(email="seller@apexind.com", password="testpassword123")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_find_buyers_page_loads_with_criteria_context(self):
        """Verify Find Buyers view loads with static criteria and dynamic custom criteria."""
        resp = self.client.get(reverse("find-buyers"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("static_criteria_list", resp.context)
        self.assertIn("custom_criteria_list", resp.context)
        self.assertIn("active_criteria_count", resp.context)

        # Check static criteria includes user requested fields
        static_keys = [item["key"] for item in resp.context["static_criteria_list"]]
        self.assertIn("entity_type", static_keys)
        self.assertIn("verified_status", static_keys)
        self.assertIn("categories_match", static_keys)
        self.assertIn("industry_match", static_keys)
        self.assertIn("specifications", static_keys)
        self.assertIn("product_matching", static_keys)

        # Content assertions
        html = resp.content.decode("utf-8")
        self.assertIn("Apply Criteria", html)
        self.assertIn("Standard Enterprise Criteria (Pre-Configured)", html)
        self.assertIn("Custom Dynamic Criteria (Key & Value Mapping)", html)
        self.assertIn("+ Add Criteria", html)

    def test_save_criteria_ajax_updates_static_and_custom_key_values(self):
        """Verify AJAX POST action=save_criteria synchronizes static checkboxes and custom key-value rows."""
        post_data = {
            "action": "save_criteria",
            "has_criteria_payload": "1",
            "static_criteria": ["entity_type", "product_matching", "verified_status"],
            "static_val_entity_type": "Pvt Ltd or Public Ltd only",
            "custom_criteria_key[]": ["OEM Experience", "Delivery SLA"],
            "custom_criteria_val[]": ["Must supply Tier-1 automotive OEMs", "Dispatch within 5 business days"],
        }
        resp = self.client.post(
            reverse("find-buyers"),
            data=post_data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        # 3 static + 2 custom = 5 active
        self.assertEqual(data["active_count"], 5)

        # Check database records
        active_params = MatchingParameter.objects.filter(company=self.company, is_active=True)
        self.assertEqual(active_params.count(), 5)

        # Check custom dynamic parameters were created with Key & Value
        oem_param = MatchingParameter.objects.filter(company=self.company, name="OEM Experience").first()
        self.assertIsNotNone(oem_param)
        self.assertEqual(oem_param.criteria_value, "Must supply Tier-1 automotive OEMs")

        sla_param = MatchingParameter.objects.filter(company=self.company, name="Delivery SLA").first()
        self.assertIsNotNone(sla_param)
        self.assertEqual(sla_param.criteria_value, "Dispatch within 5 business days")

        # Check unchecked static parameter is inactive
        cat_param = MatchingParameter.objects.filter(company=self.company, parameter_key="categories_match").first()
        self.assertIsNotNone(cat_param)
        self.assertFalse(cat_param.is_active)
