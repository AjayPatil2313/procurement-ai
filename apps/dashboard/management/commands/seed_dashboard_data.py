from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember, CompanyPermission, MemberPermission
from apps.billing.models import Subscription, CreditTransaction
from apps.catalog.models import Category, Product
from apps.requirements.models import Requirement
from apps.ai_search.models import SearchJob, ExternalCompany, SearchResult, APILog
from apps.leads.models import SavedItem, Inquiry, PriceHistory
from apps.dashboard.models import ActivityLog


class Command(BaseCommand):
    help = "Seeds database with complete RBAC accounts, ABC Trading Pvt. Ltd., and exact dashboard data from design"

    def handle(self, *args, **options):
        self.stdout.write("Seeding RBAC permissions...")

        # 1. Ensure permissions exist
        modules = [
            "companies", "members", "categories", "requirements",
            "products", "search", "results", "external_companies",
            "inquiries", "saved_items", "dashboard", "billing", "reports"
        ]
        actions = ["READ", "EDIT", "DELETE", "UPDATE", "IMPORT", "EXPORT"]
        all_perms = {}
        for m in modules:
            for a in actions:
                p, _ = CompanyPermission.objects.get_or_create(module=m, permission=a)
                all_perms[f"{m}:{a}"] = p

        # 2. Users
        self.stdout.write("Creating Users...")
        # Super Admin
        super_admin, _ = User.objects.get_or_create(
            email="admin@procurement.ai",
            defaults={
                "first_name": "System",
                "last_name": "SuperAdmin",
                "phone": "+91 90000 00001",
                "is_staff": True,
                "is_superuser": True,
                "is_email_verified": True,
            }
        )
        super_admin.set_password("Admin@12345")
        super_admin.is_staff = True
        super_admin.is_superuser = True
        super_admin.save()

        # Company Admin (Ajay Patil)
        admin_user, _ = User.objects.get_or_create(
            email="ajay@abctrading.com",
            defaults={
                "first_name": "Ajay",
                "last_name": "Patil",
                "phone": "+91 98765 43210",
                "is_staff": False,
                "is_superuser": False,
                "is_email_verified": True,
            }
        )
        admin_user.first_name = "Ajay"
        admin_user.last_name = "Patil"
        admin_user.set_password("Admin@12345")
        admin_user.save()

        # Company User (Buyer - Rahul Sharma)
        buyer_user, _ = User.objects.get_or_create(
            email="rahul@abctrading.com",
            defaults={
                "first_name": "Rahul",
                "last_name": "Sharma",
                "phone": "+91 98765 43211",
                "is_staff": False,
                "is_superuser": False,
                "is_email_verified": True,
            }
        )
        buyer_user.first_name = "Rahul"
        buyer_user.last_name = "Sharma"
        buyer_user.set_password("Admin@12345")
        buyer_user.save()

        # Company User (Sales - Priya Patel)
        sales_user, _ = User.objects.get_or_create(
            email="priya@abctrading.com",
            defaults={
                "first_name": "Priya",
                "last_name": "Patel",
                "phone": "+91 98765 43212",
                "is_staff": False,
                "is_superuser": False,
                "is_email_verified": True,
            }
        )
        sales_user.first_name = "Priya"
        sales_user.last_name = "Patel"
        sales_user.set_password("Admin@12345")
        sales_user.save()

        # 3. Company: ABC Trading Pvt. Ltd.
        self.stdout.write("Creating ABC Trading Pvt. Ltd....")
        company, _ = Company.objects.get_or_create(
            name="ABC Trading Pvt. Ltd.",
            defaults={
                "legal_name": "ABC Trading Private Limited",
                "company_type": Company.CompanyType.BOTH,
                "industry": "Manufacturing",
                "company_size": Company.CompanySize.MEDIUM_51_200,
                "website": "https://www.abctrading.com",
                "email": "contact@abctrading.com",
                "phone": "+91 22 2847 1234",
                "about": "Leading manufacturer & distributor of industrial pumps, valves, and precision parts.",
                "address_line1": "Plot 42, MIDC Industrial Area",
                "address_line2": "Andheri East",
                "city": "Mumbai",
                "state": "Maharashtra",
                "country": "India",
                "postal_code": "400093",
                "preferred_currency": "INR",
                "is_verified": True,
                "is_active": True,
                "created_by": admin_user,
            }
        )

        # Memberships
        m_admin, _ = CompanyMember.objects.update_or_create(
            user=admin_user,
            defaults={
                "company": company,
                "role": CompanyMember.Role.ADMIN,
                "is_active": True,
            }
        )

        m_buyer, _ = CompanyMember.objects.update_or_create(
            user=buyer_user,
            defaults={
                "company": company,
                "role": CompanyMember.Role.USER,
                "is_active": True,
            }
        )
        # Assign buyer permissions
        buyer_perm_keys = [
            "requirements:READ", "requirements:EDIT", "requirements:UPDATE",
            "search:READ", "search:EDIT", "results:READ", "results:EXPORT",
            "dashboard:READ", "inquiries:READ", "inquiries:EDIT"
        ]
        MemberPermission.objects.filter(member=m_buyer).delete()
        for k in buyer_perm_keys:
            if k in all_perms:
                MemberPermission.objects.get_or_create(member=m_buyer, permission=all_perms[k])

        m_sales, _ = CompanyMember.objects.update_or_create(
            user=sales_user,
            defaults={
                "company": company,
                "role": CompanyMember.Role.USER,
                "is_active": True,
            }
        )
        sales_perm_keys = [
            "products:READ", "products:EDIT", "products:UPDATE",
            "leads:READ", "leads:EDIT", "leads:UPDATE",
            "search:READ", "search:EDIT", "results:READ",
            "dashboard:READ", "reports:READ"
        ]
        MemberPermission.objects.filter(member=m_sales).delete()
        for k in sales_perm_keys:
            if k in all_perms:
                MemberPermission.objects.get_or_create(member=m_sales, permission=all_perms[k])

        # 4. Subscription & Credits
        sub, _ = Subscription.objects.update_or_create(
            company=company,
            defaults={
                "plan": Subscription.Plan.PRO,
                "credits_total": 500,
                "credits_used": 180,  # 320 remaining, exactly 64% used / remaining
                "valid_till": timezone.now().date() + timedelta(days=60),
            }
        )

        # 5. Categories
        cat_machinery, _ = Category.objects.get_or_create(name="Industrial Machinery")
        cat_metals, _ = Category.objects.get_or_create(name="Metals & Alloys")
        cat_electronics, _ = Category.objects.get_or_create(name="Electrical & Electronics")
        cat_packaging, _ = Category.objects.get_or_create(name="Packaging Materials")
        cat_furniture, _ = Category.objects.get_or_create(name="Commercial Furniture")

        # 6. Requirements (Matching Recent Searches)
        now = timezone.now()
        reqs_data = [
            ("Industrial Pump", cat_machinery, Requirement.SearchScope.GLOBAL, 42, now - timedelta(days=0, hours=2)),
            ("Steel Sheets", cat_metals, Requirement.SearchScope.NEARBY, 18, now - timedelta(days=1)),
            ("Electronic Components", cat_electronics, Requirement.SearchScope.COUNTRY, 35, now - timedelta(days=2)),
            ("Packaging Material", cat_packaging, Requirement.SearchScope.GLOBAL, 28, now - timedelta(days=3)),
            ("Office Furniture", cat_furniture, Requirement.SearchScope.NEARBY, 12, now - timedelta(days=4)),
        ]

        # External Companies (Top Suppliers from screenshot)
        suppliers_data = [
            ("GlobalTech Industries", "Mumbai, India", "mumbai@globaltech.in", 92, "https://globaltech.in"),
            ("Sunrise Manufacturing", "Shanghai, China", "sales@sunrisemfg.cn", 88, "https://sunrisemfg.cn"),
            ("Euro Components Ltd.", "Hamburg, Germany", "info@eurocomp.de", 85, "https://eurocomp.de"),
            ("Asia Industrial Supply", "Singapore", "order@asiaindustrial.sg", 78, "https://asiaindustrial.sg"),
            ("Best Electronics Co.", "Shenzhen, China", "export@bestelectronics.cn", 72, "https://bestelectronics.cn"),
        ]

        ext_companies = []
        for name, loc, email, score, web in suppliers_data:
            country = loc.split(",")[-1].strip() if "," in loc else loc
            city = loc.split(",")[0].strip() if "," in loc else ""
            c, _ = ExternalCompany.objects.get_or_create(
                name=name,
                defaults={
                    "website": web,
                    "domain": web.replace("https://", "").replace("http://", ""),
                    "email": email,
                    "phone": "+91 22 5555 1234",
                    "city": city,
                    "country": country,
                    "company_role": ExternalCompany.CompanyRole.MANUFACTURER,
                    "industry": "Industrial Supplies",
                    "description": f"Established supplier of high-quality components based in {loc}.",
                    "last_scraped_at": now - timedelta(days=2),
                }
            )
            ext_companies.append((c, score))

        # Clear old search jobs and recreate the 5 recent searches
        SearchJob.objects.filter(company=company).delete()
        for idx, (item, cat, scope, results_count, created_dt) in enumerate(reqs_data, start=1):
            rad = 50 if item == "Steel Sheets" else (100 if item == "Office Furniture" else None)
            req, _ = Requirement.objects.get_or_create(
                company=company,
                item_name=item,
                defaults={
                    "created_by": admin_user,
                    "category": cat,
                    "quantity": 100,
                    "unit": "units",
                    "target_price": 25000,
                    "currency": "INR",
                    "delivery_city": "Mumbai",
                    "delivery_country": "India",
                    "search_scope": scope,
                    "radius_km": rad,
                    "status": Requirement.Status.COMPLETED,
                    "created_at": created_dt,
                }
            )

            job = SearchJob.objects.create(
                company=company,
                user=admin_user,
                job_type=SearchJob.JobType.FIND_SUPPLIERS,
                requirement=req,
                search_query=f"{item} bulk manufacturer",
                status=SearchJob.Status.COMPLETED,
                progress_percent=100,
                total_results=results_count,
                queries_used=[f"{item} suppliers near Mumbai", f"{item} exporters India"],
                tokens_used=1240,
                api_cost=0.0150,
                started_at=created_dt - timedelta(minutes=2),
                finished_at=created_dt,
                created_at=created_dt,
            )

            # Link top suppliers to search result for Industrial Pump
            if item == "Industrial Pump":
                for ext_c, score in ext_companies:
                    res, _ = SearchResult.objects.get_or_create(
                        search_job=job,
                        external_company=ext_c,
                        defaults={
                            "result_type": SearchResult.ResultType.SUPPLIER,
                            "product_title": "Heavy Duty Industrial Pump",
                            "price": 42000,
                            "price_currency": "INR",
                            "price_converted": 42000,
                            "price_unit": "piece",
                            "moq": "2 units",
                            "distance_km": 15 if "Mumbai" in ext_c.city else 2400,
                            "is_global_cheaper": "China" in ext_c.country,
                            "savings_percent": 18.5 if "China" in ext_c.country else None,
                            "match_score": score,
                            "match_reason": f"Direct manufacturer matching specifications with verified ISO 9001 certifications.",
                            "source_url": ext_c.website,
                        }
                    )

        # 7. Products (Seller offerings)
        p1, _ = Product.objects.get_or_create(
            company=company,
            name="Industrial Centrifugal Valves",
            defaults={
                "type": Product.ItemType.PRODUCT,
                "category": cat_machinery,
                "description": "High pressure ANSI standard industrial valves for petrochemical and water processing.",
                "price_min": 15000,
                "price_max": 28000,
                "currency": "INR",
                "unit": "piece",
                "moq": 5,
                "target_industries": ["Petrochemical", "Oil & Gas", "Water Treatment"],
                "target_regions": ["India", "Middle East", "Southeast Asia"],
                "search_scope": Product.SearchScope.GLOBAL,
                "is_active": True,
            }
        )

        # 8. Activity Logs (Matching screenshot)
        ActivityLog.objects.filter(company=company).delete()
        activities = [
            (
                ActivityLog.ActivityType.SUPPLIER_FOUND,
                'New supplier found for "Industrial Pump"',
                "GlobalTech Industries was matched with 92% score.",
                "building",
                "green",
                now - timedelta(hours=2),
            ),
            (
                ActivityLog.ActivityType.LEAD_UPDATED,
                "Lead status updated to Interested",
                "Lead Metro Water Works changed to Interested.",
                "users",
                "blue",
                now - timedelta(hours=4),
            ),
            (
                ActivityLog.ActivityType.REQUIREMENT_CREATED,
                "New requirement created",
                "Created procurement requirement for Packaging Material.",
                "file-text",
                "orange",
                now - timedelta(hours=5),
            ),
            (
                ActivityLog.ActivityType.MEMBER_INVITED,
                "Team member invited",
                "Invited Priya Patel to the Sales department.",
                "user-plus",
                "purple",
                now - timedelta(days=1),
            ),
            (
                ActivityLog.ActivityType.PLAN_UPGRADED,
                "Subscription plan upgraded to Pro",
                "Plan successfully upgraded to Pro with 500 monthly credits.",
                "credit-card",
                "green",
                now - timedelta(days=2),
            ),
        ]

        for act_type, title, desc, icon, color, dt in activities:
            ActivityLog.objects.create(
                company=company,
                user=admin_user,
                activity_type=act_type,
                title=title,
                description=desc,
                icon_type=icon,
                color=color,
                created_at=dt,
            )

        self.stdout.write(self.style.SUCCESS("Database seeded successfully with exact dashboard data and RBAC!"))
