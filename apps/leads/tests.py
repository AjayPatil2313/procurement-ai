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
from apps.requirements.models import Requirement

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


class BuyerProcurementPipelineAndComparisonTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="buyer_procure@example.com",
            password="securepassword123",
            first_name="Arthur",
            last_name="Buyer",
        )
        self.company = Company.objects.create(
            name="Apex Engineering Procurement",
            company_type=Company.CompanyType.BUYER,
            industry="Manufacturing",
            created_by=self.user,
        )
        self.member = CompanyMember.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMember.Role.ADMIN,
            is_active=True,
        )

        self.category = Category.objects.create(name="Industrial Valves")
        self.requirement = Requirement.objects.create(
            company=self.company,
            created_by=self.user,
            category=self.category,
            item_name="High-Pressure Gate Valves",
            quantity=100,
            unit="units",
            target_price=Decimal("12000.00"),
            currency="INR",
            delivery_city="Pune",
            status=Requirement.Status.SEARCHING,
        )

        self.ext_company1 = ExternalCompany.objects.create(
            name="Precision Valve Corp",
            email="sales@precisionvalves.com",
            phone="+91 91234 56789",
            city="Ahmedabad",
            country="India",
        )
        self.ext_company2 = ExternalCompany.objects.create(
            name="Global Flow Systems",
            email="quotes@globalflow.com",
            phone="+91 99887 76655",
            city="Vadodara",
            country="India",
        )

        self.search_job = SearchJob.objects.create(
            company=self.company,
            requirement=self.requirement,
            job_type=SearchJob.JobType.FIND_SUPPLIERS,
            status=SearchJob.Status.COMPLETED,
        )

        self.search_result1 = SearchResult.objects.create(
            search_job=self.search_job,
            external_company=self.ext_company1,
            product_title="Forged Gate Valve ANSI 600",
            result_type=SearchResult.ResultType.SUPPLIER,
            price=Decimal("10500.00"),
            match_score=95,
            match_reason="Matches industrial high pressure gate valve specifications",
        )
        self.search_result2 = SearchResult.objects.create(
            search_job=self.search_job,
            external_company=self.ext_company2,
            product_title="Cast Steel Gate Valve 150#",
            result_type=SearchResult.ResultType.SUPPLIER,
            price=Decimal("13000.00"),
            match_score=88,
            match_reason="Matches cast steel gate valve specs",
        )

        self.client = Client()
        self.client.login(email="buyer_procure@example.com", password="securepassword123")
        session = self.client.session
        session["active_company_id"] = self.company.id
        session.save()

    def test_saved_suppliers_toggle_and_deduplication(self):
        """Test bookmarking a supplier creates a SavedItem and prevents duplicates."""
        url = reverse("save-supplier-toggle", kwargs={"result_id": self.search_result1.id})

        # 1. First save via AJAX
        response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["created"])
        self.assertEqual(SavedItem.objects.filter(company=self.company).count(), 1)

        # 2. Second save attempt triggers deduplication
        dup_response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(dup_response.status_code, 200)
        dup_data = dup_response.json()
        self.assertTrue(dup_data["success"])
        self.assertFalse(dup_data["created"])
        self.assertEqual(SavedItem.objects.filter(company=self.company).count(), 1)

    def test_saved_suppliers_list_and_ajax_update(self):
        """Test viewing shortlisted suppliers and updating pipeline stage / notes via AJAX."""
        saved_item = SavedItem.objects.create(
            company=self.company,
            search_result=self.search_result1,
            status=SavedItem.Status.INTERESTED,
        )

        # 1. View list with requirement filter
        list_url = reverse("saved-suppliers") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Precision Valve Corp")
        self.assertEqual(response.context["total_saved_count"], 1)

        # 2. Update status and notes via AJAX
        update_url = reverse("update-saved-supplier", kwargs={"item_id": saved_item.id})
        update_res = self.client.post(update_url, {
            "status": "contacted",
            "notes": "Sent formal RFQ inquiry for 100 units.",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(update_res.status_code, 200)
        data = update_res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "contacted")
        self.assertEqual(data["notes"], "Sent formal RFQ inquiry for 100 units.")

        saved_item.refresh_from_db()
        self.assertEqual(saved_item.status, SavedItem.Status.CONTACTED)
        self.assertEqual(saved_item.notes, "Sent formal RFQ inquiry for 100 units.")

    def test_export_saved_suppliers_csv(self):
        """Test 1-click CSV export for shortlisted suppliers."""
        SavedItem.objects.create(
            company=self.company,
            search_result=self.search_result1,
            status=SavedItem.Status.CONTACTED,
            notes="Sample procurement note",
        )

        url = reverse("export-saved-suppliers-csv") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Shortlisted_Suppliers", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Precision Valve Corp", content)
        self.assertIn("High-Pressure Gate Valves", content)

    def test_price_comparison_view_and_calculations(self):
        """Test price comparison dashboard calculates variance, savings %, and handles verified quotes vs web prices."""
        # Record formal quote for supplier 1 via Inquiry
        Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result1,
            subject="RFQ: Gate Valves",
            message="Quotation required",
            sent_to_email="sales@precisionvalves.com",
            quoted_price=Decimal("10200.00"),
            quoted_currency="INR",
            delivery_terms="Door Delivery, 10 Days",
            status=Inquiry.Status.REPLIED,
        )

        url = reverse("price-comparison") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_requirement"], self.requirement)
        self.assertEqual(response.context["target_price"], Decimal("12000.00"))

        comparison_items = response.context["comparison_items"]
        self.assertEqual(len(comparison_items), 2)

        # Supplier 1: formal quote 10200 vs target 12000 (savings = 1800, 15.0%)
        sup1 = next(item for item in comparison_items if item["company"].id == self.ext_company1.id)
        self.assertTrue(sup1["is_verified_quote"])
        self.assertEqual(sup1["price"], Decimal("10200.00"))
        self.assertEqual(sup1["variance"], Decimal("-1800.00"))
        self.assertEqual(sup1["savings_percent"], 15.0)

        # Supplier 2: discovered web price 13000 vs target 12000 (variance = +1000)
        sup2 = next(item for item in comparison_items if item["company"].id == self.ext_company2.id)
        self.assertFalse(sup2["is_verified_quote"])
        self.assertEqual(sup2["price"], Decimal("13000.00"))
        self.assertEqual(sup2["variance"], Decimal("1000.00"))
        self.assertIsNone(sup2["savings_percent"])

        # Check KPIs
        self.assertEqual(response.context["lowest_price"], Decimal("10200.00"))
        self.assertEqual(response.context["max_savings_pct"], 15.0)
        self.assertEqual(response.context["cheaper_options_count"], 1)

    def test_export_price_comparison_csv(self):
        """Test dedicated 1-click CSV export for price comparison matrix."""
        Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result1,
            subject="RFQ: Gate Valves",
            message="Quotation required",
            sent_to_email="sales@precisionvalves.com",
            quoted_price=Decimal("10200.00"),
            quoted_currency="INR",
            delivery_terms="Door Delivery, 10 Days",
            status=Inquiry.Status.REPLIED,
        )

        url = reverse("export-price-comparison-csv") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Price_Comparison_", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Precision Valve Corp", content)
        self.assertIn("10200.00", content)
        self.assertIn("12000.00", content)
        self.assertIn("Verified Formal Quote", content)

    def test_buyer_inquiries_list_and_export_csv(self):
        """Test Buyer inquiries view lists requirement-linked RFQs and exports Buyer CSV."""
        inq = Inquiry.objects.create(
            company=self.company,
            search_result=self.search_result1,
            subject="Commercial Inquiry: High-Pressure Valves",
            message="Please provide terms",
            sent_to_email="sales@precisionvalves.com",
            quoted_price=Decimal("10200.00"),
            quoted_currency="INR",
            status=Inquiry.Status.SENT,
        )
        inq.ensure_initial_message()

        # 1. Inquiries list view in buyer context
        list_url = reverse("inquiries-list") + f"?requirement_id={self.requirement.id}"
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Precision Valve Corp")
        self.assertIn("requirements", response.context)

        # 2. Export inquiries CSV in buyer context
        export_url = reverse("export-inquiries-csv") + f"?requirement_id={self.requirement.id}"
        export_res = self.client.get(export_url)
        self.assertEqual(export_res.status_code, 200)
        self.assertEqual(export_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Procurement_Inquiries", export_res["Content-Disposition"])

        content = export_res.content.decode("utf-8")
        self.assertIn("High-Pressure Gate Valves", content)
        self.assertIn("Precision Valve Corp", content)

    def test_buyer_export_reports_view_and_csv(self):
        """Test Buyer export reports dashboard displays requirements catalog and downloads CSV."""
        url = reverse("export-reports")

        # 1. Landing view with requirement selected
        response = self.client.get(url, {"report_type": "leads", "requirement_id": self.requirement.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Precision Valve Corp")
        self.assertFalse(response.context["is_seller_context"])
        self.assertGreaterEqual(len(response.context["preview_records"]), 1)

        # 2. Download CSV for Supplier Intelligence
        csv_res = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "leads",
            "requirement_id": self.requirement.id,
        })
        self.assertEqual(csv_res.status_code, 200)
        self.assertEqual(csv_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Supplier_Discovery_Report", csv_res["Content-Disposition"])
        self.assertIn("Precision Valve Corp", csv_res.content.decode("utf-8"))

        # 3. Download CSV for Requirement Specifications
        specs_res = self.client.get(url, {
            "download": "1",
            "format": "csv",
            "report_type": "catalog",
            "requirement_id": self.requirement.id,
        })
        self.assertEqual(specs_res.status_code, 200)
        self.assertEqual(specs_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Requirement_Specifications", specs_res["Content-Disposition"])
        self.assertIn("High-Pressure Gate Valves", specs_res.content.decode("utf-8"))



