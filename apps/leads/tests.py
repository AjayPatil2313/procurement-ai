from decimal import Decimal
import json
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from apps.companies.models import Company, CompanyMember
from apps.catalog.models import Product, Category
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany
from apps.leads.models import Inquiry, InquiryMessage, SavedItem
from apps.leads.services.email_service import send_inquiry_email

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

        # Verify real email was dispatched via email backend
        from django.core import mail
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, ["procurement@globalmfg.com"])
        self.assertIn("Commercial Proposal", sent_email.body)
        self.assertIn("850,000.00", sent_email.body)

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
        """Test dynamic export reports dashboard gates data by product and returns preview records."""
        url = reverse("sales-export-reports")
        
        # 1. Without product selection: displays product selection prompt card and empty preview
        response = self.client.get(url, {"report_type": "leads"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a Product to Generate Intelligence")
        self.assertEqual(len(response.context["preview_records"]), 0)

        # 2. With product selection: loads product dossier, metrics, and preview records
        response_prod = self.client.get(url, {"report_type": "leads", "product_id": self.product.id})
        self.assertEqual(response_prod.status_code, 200)
        self.assertContains(response_prod, "Global Manufacturing Corp")
        self.assertContains(response_prod, "Download CSV")
        self.assertContains(response_prod, "Print Report")
        self.assertNotContains(response_prod, "Export JSON")
        self.assertIn("preview_records", response_prod.context)
        self.assertGreaterEqual(len(response_prod.context["preview_records"]), 1)

        # 3. Inquiries report type preview with product selected
        inq_res = self.client.get(url, {"report_type": "inquiries", "product_id": self.product.id})
        self.assertEqual(inq_res.status_code, 200)
        self.assertEqual(inq_res.context["report_type"], "inquiries")
        self.assertIn("preview_records", inq_res.context)

    def test_export_reports_csv_and_json(self):
        """Test product-scoped CSV download and redirection when no product is chosen."""
        url = reverse("sales-export-reports")

        # 1. Download attempt without product_id redirects with prompt
        unselected_res = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "leads",
        })
        self.assertEqual(unselected_res.status_code, 302)

        # 2. CSV Download with product_id (leads)
        csv_response = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "leads",
            "product_id": self.product.id,
        })
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", csv_response["Content-Disposition"])
        self.assertIn("Global Manufacturing Corp", csv_response.content.decode("utf-8"))

        # 3. CSV Download with product_id (inquiries)
        csv_inq_res = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "inquiries",
            "product_id": self.product.id,
        })
        self.assertEqual(csv_inq_res.status_code, 200)
        self.assertEqual(csv_inq_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Inquiries_Audit", csv_inq_res["Content-Disposition"])

    def test_send_proposal_with_file_attachment(self):
        """Test dispatching commercial proposal with uploaded quotation PDF document."""
        from django.core import mail
        mail.outbox.clear()

        fake_pdf = SimpleUploadedFile(
            "formal_quotation_2026.pdf",
            b"%PDF-1.4 sample commercial quotation binary bytes",
            content_type="application/pdf",
        )

        url = reverse("sales-send-inquiry", kwargs={"result_id": self.search_result.id})
        response = self.client.post(url, {
            "subject": "Commercial Offer & Quotation Attachment",
            "message": "Please review the attached formal quotation and technical data sheet.",
            "sent_to_email": "purchasing@globalmfg.com",
            "target_price": "920000.00",
            "delivery_terms": "Ex-Factory, 14 Days",
            "attachment": fake_pdf,
        })
        self.assertEqual(response.status_code, 302)

        inquiry = Inquiry.objects.filter(sent_to_email="purchasing@globalmfg.com").first()
        self.assertIsNotNone(inquiry)
        self.assertTrue(bool(inquiry.attachment))
        self.assertEqual(inquiry.attachment_name, "formal_quotation_2026.pdf")
        self.assertEqual(inquiry.get_attachment_extension(), "pdf")
        self.assertTrue("formal_quotation_2026.pdf" in inquiry.get_attachment_filename())

        # Thread initial message also has attachment
        initial_msg = inquiry.messages.first()
        self.assertIsNotNone(initial_msg)
        self.assertTrue(bool(initial_msg.attachment))
        self.assertEqual(initial_msg.attachment_name, "formal_quotation_2026.pdf")

        # Verify email was dispatched with physical attachment
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, ["purchasing@globalmfg.com"])
        self.assertIn("formal_quotation_2026.pdf", sent_email.body)
        self.assertEqual(len(sent_email.attachments), 1)
        att_filename, att_content, att_mimetype = sent_email.attachments[0]
        self.assertEqual(att_filename, "formal_quotation_2026.pdf")
        self.assertEqual(att_content, b"%PDF-1.4 sample commercial quotation binary bytes")

    def test_add_reply_message_with_attachment(self):
        """Test appending follow-up reply with revision attachment and email copy."""
        from django.core import mail
        mail.outbox.clear()

        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Negotiation Phase",
            message="Initial terms",
            sent_to_email="procurement@globalmfg.com",
            status=Inquiry.Status.IN_DISCUSSION,
        )

        fake_excel = SimpleUploadedFile(
            "revised_pricing_matrix.xlsx",
            b"fake excel matrix binary content",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        url = reverse("sales-inquiry-add-message", kwargs={"pk": inquiry.id})
        response = self.client.post(url, {
            "message_body": "Enclosed is our revised volume tiered pricing breakdown.",
            "message_type": "outbound",
            "send_email_copy": "1",
            "attachment": fake_excel,
        })
        self.assertEqual(response.status_code, 302)

        last_msg = inquiry.messages.last()
        self.assertIsNotNone(last_msg)
        self.assertTrue(bool(last_msg.attachment))
        self.assertEqual(last_msg.attachment_name, "revised_pricing_matrix.xlsx")
        self.assertEqual(last_msg.get_attachment_extension(), "xlsx")

        # Verify email copy included the attachment
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(len(sent_email.attachments), 1)
        att_filename, att_content, _ = sent_email.attachments[0]
        self.assertEqual(att_filename, "revised_pricing_matrix.xlsx")
        self.assertEqual(att_content, b"fake excel matrix binary content")

    def test_email_service_direct_dispatch_with_attachment(self):
        """Test send_inquiry_email service function directly with attachment."""
        from django.core import mail
        mail.outbox.clear()

        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Direct Service Dispatch",
            message="Direct dispatch test body",
            sent_to_email="direct@vendor.com",
            quoted_price=Decimal("450000.00"),
            quoted_currency="INR",
            delivery_terms="Door Delivery, 5 Days",
        )

        fake_doc = SimpleUploadedFile(
            "technical_specs.pdf",
            b"%PDF specs binary bytes",
            content_type="application/pdf",
        )

        success, note = send_inquiry_email(
            inquiry,
            user=self.user,
            attachment_file=fake_doc,
        )
        self.assertTrue(success)
        self.assertEqual(note, "Email successfully dispatched to recipient.")

        self.assertGreaterEqual(len(mail.outbox), 1)
        dispatched = mail.outbox[-1]
        self.assertEqual(dispatched.to, ["direct@vendor.com"])
        self.assertEqual(dispatched.reply_to, [self.user.email])
        self.assertEqual(len(dispatched.attachments), 1)
        self.assertEqual(dispatched.attachments[0][0], "technical_specs.pdf")

    def test_export_inquiries_csv_view(self):
        """Test dedicated 1-click inquiries CSV export endpoint."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Commercial Supply Agreement",
            message="Initial supply quote request",
            sent_to_email="procurement@globalmfg.com",
            quoted_price=Decimal("820000.00"),
            quoted_currency="INR",
            delivery_terms="CIF Mumbai",
            status=Inquiry.Status.SENT,
        )
        inquiry.ensure_initial_message()

        url = reverse("sales-export-inquiries-csv")
        response = self.client.get(url, {"product_id": self.product.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertIn("Sales_Inquiries", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Inquiry ID", content)
        self.assertIn("Counterparty Company", content)
        self.assertIn("Global Manufacturing Corp", content)
        self.assertIn("820000.00", content)
        self.assertIn("CIF Mumbai", content)

    def test_inquiries_list_view_query_optimization(self):
        """Test inquiries_list_view annotates messages_count without N+1 queries."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="Thread Performance Test",
            message="Testing query count",
            sent_to_email="procurement@globalmfg.com",
            status=Inquiry.Status.SENT,
        )
        inquiry.ensure_initial_message()

        url = reverse("sales-inquiries-list")
        response = self.client.get(url, {"product_id": self.product.id})
        self.assertEqual(response.status_code, 200)
        inqs = response.context["inquiries"]
        self.assertGreaterEqual(len(inqs), 1)
        # Check annotated messages_count exists and matches
        first_inq = inqs[0]
        self.assertTrue(hasattr(first_inq, "messages_count"))
        self.assertGreaterEqual(first_inq.messages_count, 1)

    def test_record_inquiry_quote_ajax(self):
        """Test recording quotation terms via AJAX returns JSON payload."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="AJAX Quote Proposal",
            message="Pending Quote",
            sent_to_email="purchasing@globalmfg.com",
            status=Inquiry.Status.SENT,
        )

        url = reverse("sales-record-inquiry-quote", kwargs={"pk": inquiry.id})
        response = self.client.post(url, {
            "quoted_price": "675000.00",
            "currency": "INR",
            "delivery_terms": "Door Delivery, 7 Days",
            "notes": "100% against delivery",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], Inquiry.Status.REPLIED)
        self.assertEqual(data["quoted_price"], "675000.00")
        self.assertEqual(data["currency"], "INR")

    def test_add_inquiry_message_ajax(self):
        """Test adding follow-up message via AJAX returns JSON payload."""
        inquiry = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result,
            subject="AJAX Message Thread",
            message="Initial text",
            sent_to_email="purchasing@globalmfg.com",
            status=Inquiry.Status.SENT,
        )
        inquiry.ensure_initial_message()

        url = reverse("sales-inquiry-add-message", kwargs={"pk": inquiry.id})
        response = self.client.post(url, {
            "message_body": "Checking in on the payment schedule terms.",
            "message_type": "outbound",
            "new_status": "in_discussion",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], Inquiry.Status.IN_DISCUSSION)


