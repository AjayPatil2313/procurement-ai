from decimal import Decimal
import json
import csv
import io
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from apps.companies.models import Company, CompanyMember
from apps.catalog.models import Category
from apps.requirements.models import Requirement
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany, MatchingParameter
from apps.leads.models import SavedItem

User = get_user_model()


class BuyerRequirementsAndSourcingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="buyer@example.com",
            password="securepassword123",
            first_name="John",
            last_name="Buyer",
        )
        self.company = Company.objects.create(
            name="Apex Procurement Corp",
            company_type=Company.CompanyType.BUYER,
            industry="Automotive",
            city="Chennai",
            created_by=self.user,
        )
        self.member = CompanyMember.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        # Other company for multi-tenant isolation test
        self.other_user = User.objects.create_user(
            email="rival@example.com",
            password="securepassword123",
            first_name="Rival",
            last_name="Buyer",
        )
        self.other_company = Company.objects.create(
            name="Rival Enterprises",
            company_type=Company.CompanyType.BUYER,
            industry="Machinery",
            city="Mumbai",
            created_by=self.other_user,
        )
        self.other_member = CompanyMember.objects.create(
            user=self.other_user,
            company=self.other_company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.category = Category.objects.create(name="Automotive Parts")

        self.requirement = Requirement.objects.create(
            company=self.company,
            created_by=self.user,
            category=self.category,
            item_name="Precision Ball Bearings 6205",
            quantity=5000,
            unit="units",
            target_price=Decimal("150.00"),
            currency="INR",
            delivery_city="Chennai",
            search_scope="nearby",
            specifications="High carbon chromium steel, ISO 9001 certified",
            status=Requirement.Status.SEARCHING,
        )

        self.client = Client()
        self.client.login(email="buyer@example.com", password="securepassword123")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_requirements_list_and_active_tabs(self):
        """Test requirements list view with metrics and Active tab."""
        url = reverse("requirements-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Precision Ball Bearings 6205")
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["active_count"], 1)
        self.assertEqual(response.context["archived_count"], 0)
        self.assertFalse(response.context["is_archived_view"])

    def test_requirement_create_view(self):
        """Test creating a new requirement with validation and multi-tenant assignment."""
        url = reverse("requirements-create")
        post_data = {
            "item_name": "Hydraulic Brake Cylinders",
            "category": self.category.id,
            "quantity": 1200,
            "unit": "sets",
            "target_price": "850.00",
            "currency": "INR",
            "delivery_city": "Coimbatore",
            "search_scope": "country",
            "specifications": "Pressure tested 150 bar, DOT4 compatible",
            "description": "Commercial supply for Q1 production run",
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        new_req = Requirement.objects.filter(item_name="Hydraulic Brake Cylinders").first()
        self.assertIsNotNone(new_req)
        self.assertEqual(new_req.company, self.company)
        self.assertEqual(new_req.quantity, 1200)
        self.assertEqual(new_req.target_price, Decimal("850.00"))

    def test_requirement_soft_delete_and_restore(self):
        """Test soft-delete moves item to archived trash and restore reactivates it."""
        delete_url = reverse("requirement-delete", kwargs={"pk": self.requirement.id})
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)

        self.requirement.refresh_from_db()
        self.assertTrue(self.requirement.is_deleted)

        # Check Archived view
        list_url = reverse("requirements-list") + "?status=archived"
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_archived_view"])
        self.assertEqual(response.context["archived_count"], 1)
        self.assertEqual(response.context["active_count"], 0)
        self.assertContains(response, "Archived / Trash")

        # Restore
        restore_url = reverse("requirements-restore", kwargs={"pk": self.requirement.id})
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 302)

        self.requirement.refresh_from_db()
        self.assertFalse(self.requirement.is_deleted)

    def test_requirements_company_isolation(self):
        """Test strict company isolation: rival company cannot access or delete Apex's requirements."""
        rival_client = Client()
        rival_client.login(email="rival@example.com", password="securepassword123")
        session = rival_client.session
        session["active_company_id"] = self.other_company.id
        session.save()

        # Rival view list should not see Apex's requirement
        response = rival_client.get(reverse("requirements-list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Precision Ball Bearings 6205")

        # Rival cannot detail view Apex's requirement
        detail_url = reverse("requirement-detail", kwargs={"pk": self.requirement.id})
        response = rival_client.get(detail_url)
        self.assertEqual(response.status_code, 404)

        # Rival cannot soft delete Apex's requirement
        delete_url = reverse("requirement-delete", kwargs={"pk": self.requirement.id})
        response = rival_client.post(delete_url)
        self.assertEqual(response.status_code, 404)
        self.requirement.refresh_from_db()
        self.assertFalse(self.requirement.is_deleted)

    def test_requirements_export_csv(self):
        """Test exporting requirements to CSV with proper headers and data."""
        url = reverse("requirements-export-csv")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Requirements_Apex_Procurement_Corp", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Precision Ball Bearings 6205", content)
        self.assertIn("Automotive Parts", content)
        self.assertIn("150.00", content)

    def test_requirements_import_template_and_bulk_import(self):
        """Test downloading sample template and bulk importing requirements from CSV."""
        # 1. Download template
        template_url = reverse("requirements-import-template")
        template_resp = self.client.get(template_url)
        self.assertEqual(template_resp.status_code, 200)
        self.assertIn("Requirements_Sample_Template.csv", template_resp["Content-Disposition"])

        # 2. Upload CSV
        csv_content = (
            "Item Name,Category,Quantity,Unit,Target Price,Currency,Delivery City,Search Scope,Specifications,Description\n"
            "Heavy Duty Transmission Gears,Automotive Parts,800,pieces,1250.00,INR,Chennai,nearby,Case hardened steel,Drive axle gears\n"
        )
        csv_file = SimpleUploadedFile("test_reqs.csv", csv_content.encode("utf-8"), content_type="text/csv")

        import_url = reverse("requirements-import-csv")
        response = self.client.post(import_url, {"csv_file": csv_file})
        self.assertEqual(response.status_code, 302)

        imported_req = Requirement.objects.filter(item_name="Heavy Duty Transmission Gears").first()
        self.assertIsNotNone(imported_req)
        self.assertEqual(imported_req.company, self.company)
        self.assertEqual(imported_req.quantity, 800)
        self.assertEqual(imported_req.target_price, Decimal("1250.00"))

    def test_find_suppliers_save_criteria_ajax(self):
        """Test saving dynamic matching criteria via AJAX on Find Suppliers page."""
        url = reverse("find-suppliers")
        post_data = {
            "action": "save_criteria",
            "requirement_id": self.requirement.id,
            "has_criteria_payload": "1",
            "static_criteria": ["specifications", "entity_type"],
            "static_val_specifications": "ISO 9001:2015, IATF 16949",
            "custom_criteria_key[]": ["Heat Treatment Facility", "Warranty Period"],
            "custom_criteria_val[]": ["In-house vacuum carburizing", "24 Months Full Replacement"],
        }
        response = self.client.post(url, post_data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["active_count"], 4)

        # Verify MatchingParameter objects in DB
        params = MatchingParameter.objects.filter(company=self.company)
        self.assertTrue(params.filter(parameter_key="specifications", is_active=True).exists())
        self.assertTrue(params.filter(name="Heat Treatment Facility").exists())
        self.assertTrue(params.filter(name="Warranty Period").exists())

    def test_export_discovered_suppliers_csv(self):
        """Test 1-click CSV download of discovered suppliers."""
        ext = ExternalCompany.objects.create(
            name="Apex Precision Bearings Ltd",
            email="sales@apexbearings.in",
            city="Coimbatore",
            country="India",
        )
        job = SearchJob.objects.create(
            company=self.company,
            requirement=self.requirement,
            status=SearchJob.Status.COMPLETED,
        )
        SearchResult.objects.create(
            search_job=job,
            external_company=ext,
            product_title="Ball Bearings 6205-2RS",
            result_type=SearchResult.ResultType.SUPPLIER,
            price=Decimal("142.50"),
            match_score=94,
        )

        url = reverse("export-discovered-suppliers-csv") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Discovered_Suppliers", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Apex Precision Bearings Ltd", content)
        self.assertIn("sales@apexbearings.in", content)
        self.assertIn("142.50", content)
