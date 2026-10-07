from decimal import Decimal
import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.companies.models import Company, CompanyMember
from apps.catalog.models import Product, Category
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany
from apps.leads.models import Inquiry, InquiryMessage, SavedItem

User = get_user_model()


class DynamicInquiriesAndReportsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="seller@example.com",
            password="securepassword123",
            first_name="Jane",
            last_name="Seller",
        )
        self.company = Company.objects.create(
            name="Apex Dynamics Ltd",
            company_type=Company.CompanyType.SELLER,
            industry="Machinery",
            created_by=self.user,
        )
        self.member = CompanyMember.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.category = Category.objects.create(name="Heavy Machinery")
        self.product = Product.objects.create(
            company=self.company,
            category=self.category,
            name="Industrial CNC Milling Unit",
            minimum_order_quantity=2,
            unit="units",
        )

        self.ext_company = ExternalCompany.objects.create(
            name="Global Manufacturing Corp",
            email="purchasing@globalmfg.com",
            phone="+91 98765 43210",
            city="Pune",
            country="India",
        )

        self.search_job = SearchJob.objects.create(
            company=self.company,
            product=self.product,
            status=SearchJob.Status.COMPLETED,
        )

        self.search_result = SearchResult.objects.create(
            search_job=self.search_job,
            external_company=self.ext_company,
            product_title="High Precision CNC Mill",
            result_type=SearchResult.ResultType.LEAD,
            match_score=92,
            need_signal="Procuring 3 CNC milling centers for Q4 expansion",
            match_reason="Matches industrial milling machinery requirements",
        )

        self.client = Client()
        self.client.login(email="seller@example.com", password="securepassword123")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_send_inquiry_and_initial_message(self):
        """Test dispatching an inquiry creates the inquiry and the initial conversation message."""
        url = reverse("sales-send-inquiry", kwargs={"result_id": self.search_result.id})
        response = self.client.post(url, {
            "subject": "Commercial Supply Proposal: CNC Milling",
            "message": "We would like to supply our high-precision CNC units.",
            "sent_to_email": "procurement@globalmfg.com",
            "target_price": "850000.00",
            "delivery_terms": "Ex-Factory, 14 Days",
        })
        self.assertEqual(response.status_code, 302)

        inquiry = Inquiry.objects.filter(company=self.company, search_result=self.search_result).first()
        self.assertIsNotNone(inquiry)
        self.assertEqual(inquiry.subject, "Commercial Supply Proposal: CNC Milling")
        self.assertEqual(inquiry.quoted_price, Decimal("850000.00"))
        self.assertEqual(inquiry.delivery_terms, "Ex-Factory, 14 Days")
        self.assertEqual(inquiry.status, Inquiry.Status.SENT)

        # Ensure initial conversation message is logged
        messages = inquiry.messages.all()
        self.assertGreaterEqual(messages.count(), 1)
        self.assertEqual(messages.first().message_type, InquiryMessage.MessageType.OUTBOUND)

    def test_inquiry_message_and_thread(self):
        """Test appending follow-up messages to the inquiry stream."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Proposal",
            message="Initial proposal body",
            sent_to_email="procurement@globalmfg.com",
            status=Inquiry.Status.SENT,
        )
        inquiry.ensure_initial_message()

        url = reverse("sales-inquiry-add-message", kwargs={"pk": inquiry.id})
        response = self.client.post(url, {
            "message_body": "Checking in regarding our technical specsheet sent last Tuesday.",
            "message_type": "outbound",
            "sender_name": "Jane Seller",
            "new_status": "in_discussion",
        })
        self.assertEqual(response.status_code, 302)

        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, Inquiry.Status.IN_DISCUSSION)
        self.assertEqual(inquiry.messages.count(), 2)  # initial + follow-up

    def test_update_inquiry_status_ajax(self):
        """Test inline AJAX status updates from table rows."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Proposal",
            message="Initial proposal",
            sent_to_email="procurement@globalmfg.com",
            status=Inquiry.Status.SENT,
        )

        url = reverse("sales-inquiry-update-status", kwargs={"pk": inquiry.id})
        response = self.client.post(
            url,
            {"status": "won"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "won")

        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, Inquiry.Status.WON)

    def test_record_inquiry_quote(self):
        """Test recording quotation terms and advancing status to REPLIED."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Proposal",
            message="Initial proposal",
            sent_to_email="procurement@globalmfg.com",
            status=Inquiry.Status.SENT,
        )

        url = reverse("sales-record-inquiry-quote", kwargs={"pk": inquiry.id})
        response = self.client.post(url, {
            "quoted_price": "795000.00",
            "currency": "INR",
            "delivery_terms": "CIF Mumbai, 10 Days Delivery",
            "notes": "50% advance, balance against BL copy.",
        })
        self.assertEqual(response.status_code, 302)

        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, Inquiry.Status.REPLIED)
        self.assertEqual(inquiry.quoted_price, Decimal("795000.00"))
        self.assertEqual(inquiry.quoted_currency, "INR")
        self.assertEqual(inquiry.delivery_terms, "CIF Mumbai, 10 Days Delivery")

    def test_export_reports_view_and_preview(self):
        """Test dynamic export reports dashboard returns preview records."""
        url = reverse("sales-export-reports")
        response = self.client.get(url, {"report_type": "leads"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Global Manufacturing Corp")
        self.assertContains(response, "Download CSV")
        self.assertNotContains(response, "Export JSON")
        self.assertIn("preview_records", response.context)
        self.assertGreaterEqual(len(response.context["preview_records"]), 1)

        # Fallback test: 'saved' or 'consolidated' defaults to 'leads'
        fallback_res = self.client.get(url, {"report_type": "saved"})
        self.assertEqual(fallback_res.status_code, 200)
        self.assertEqual(fallback_res.context["report_type"], "leads")

        # Inquiries report type preview
        inq_res = self.client.get(url, {"report_type": "inquiries"})
        self.assertEqual(inq_res.status_code, 200)
        self.assertEqual(inq_res.context["report_type"], "inquiries")
        self.assertIn("preview_records", inq_res.context)

    def test_export_reports_csv_and_json(self):
        """Test multi-format export downloads for CSV and JSON."""
        url = reverse("sales-export-reports")

        # 1. CSV Download (leads)
        csv_response = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "leads",
        })
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", csv_response["Content-Disposition"])
        self.assertIn("Global Manufacturing Corp", csv_response.content.decode("utf-8"))

        # 2. JSON Export (leads)
        json_response = self.client.get(url, {
            "download": "1",
            "format": "json",
            "report_type": "leads",
        })
        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(json_response["Content-Type"], "application/json")
        data = json.loads(json_response.content.decode("utf-8"))
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["company_name"], "Global Manufacturing Corp")

        # 3. CSV Download (inquiries)
        csv_inq_res = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "inquiries",
        })
        self.assertEqual(csv_inq_res.status_code, 200)
        self.assertEqual(csv_inq_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Inquiries_Audit", csv_inq_res["Content-Disposition"])

        # 4. JSON Export (inquiries)
        json_inq_res = self.client.get(url, {
            "download": "1",
            "format": "json",
            "report_type": "inquiries",
        })
        self.assertEqual(json_inq_res.status_code, 200)
        self.assertEqual(json_inq_res["Content-Type"], "application/json")
