import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.utils import timezone
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany, APILog, MatchingParameter, ensure_default_parameters_for_company
from apps.ai_search.services.search_provider import WebSearchProvider
from apps.ai_search.services.web_scraper import CompanyWebScraper
from apps.ai_search.services.ai_matcher import AIMatcher
from apps.catalog.models import Product
from apps.requirements.models import Requirement
from apps.billing.models import Subscription, CreditTransaction
from apps.billing.services.wallet import CreditWalletService

logger = logging.getLogger(__name__)

DUMMY_PHONES = {"+91 22 2840 5000", "+91 22 5555 1234"}


def _get_or_scrape_company(cand: dict) -> dict:
    """Returns cached company data if genuine and available in DB, else scrapes target website."""
    url = cand.get("url", "")
    domain = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    if domain:
        cached = ExternalCompany.objects.filter(domain=domain).first()
        # Ensure cached record is not contaminated with legacy dummy values
        if cached and cached.phone not in DUMMY_PHONES:
            if cached.name and (cached.email or cached.phone):
                return {
                    "name": cached.name,
                    "domain": cached.domain,
                    "website": cached.website or url,
                    "email": cached.email,
                    "phone": cached.phone,
                    "address": cached.address,
                    "city": cached.city,
                    "state": cached.state,
                    "country": cached.country,
                    "company_role": cached.company_role,
                    "industry": cached.industry,
                    "description": cached.description or cand.get("snippet", ""),
                }

    scraper = CompanyWebScraper(timeout=3.5)
    scraped = scraper.scrape(
        url=url,
        snippet_title=cand.get("title", ""),
        snippet_text=cand.get("snippet", ""),
    )

    # If live site blocked bots but discovery candidate has genuine contact details, enrich:
    if not scraped.get("phone") and cand.get("phone") and cand["phone"] not in DUMMY_PHONES:
        scraped["phone"] = cand["phone"]
    if not scraped.get("email") and cand.get("email"):
        scraped["email"] = cand["email"]
    elif not scraped.get("email") and domain:
        scraped["email"] = f"info@{domain}"
    if not scraped.get("city") and cand.get("city"):
        scraped["city"] = cand["city"]
    if not scraped.get("state") and cand.get("state"):
        scraped["state"] = cand["state"]
    if not scraped.get("address") and scraped.get("city"):
        state_part = f", {scraped['state']}" if scraped.get("state") else ""
        scraped["address"] = f"{scraped['city']}{state_part}, {scraped.get('country', 'India')}"

    return scraped


def _process_buyer_candidate(cand: dict, product: Product, matching_parameters: list | None = None) -> tuple[dict, dict, dict]:
    """Scrapes company (with cache check) and runs AI fit evaluation in parallel worker thread."""
    scraped = _get_or_scrape_company(cand)
    fit = AIMatcher.evaluate_fit(
        scraped_company=scraped,
        target_item_name=product.name,
        target_specs=product.specifications or "",
        target_price=product.price,
        currency=product.currency or "INR",
        job_type="find_buyers",
        matching_parameters=matching_parameters,
    )
    return cand, scraped, fit


def _process_supplier_candidate(cand: dict, requirement: Requirement) -> tuple[dict, dict, dict]:
    """Scrapes supplier (with cache check) and runs AI fit evaluation in parallel worker thread."""
    scraped = _get_or_scrape_company(cand)
    fit = AIMatcher.evaluate_fit(
        scraped_company=scraped,
        target_item_name=requirement.item_name,
        target_specs=requirement.specifications or "",
        target_price=requirement.target_price,
        currency=requirement.currency or "INR",
        job_type="find_suppliers",
    )
    return cand, scraped, fit


def _save_or_update_external_company(cand: dict, scraped: dict, default_role: str) -> ExternalCompany:
    """Saves or updates ExternalCompany with clean, verified scraped data."""
    domain_key = scraped.get("domain") or cand.get("url", "")
    ext_company = ExternalCompany.objects.filter(domain=domain_key).first()

    name = scraped.get("name") or cand.get("title", "Industrial Enterprise")
    website = scraped.get("website") or cand.get("url", "")
    email = scraped.get("email", "")
    phone = scraped.get("phone", "")
    if phone in DUMMY_PHONES:
        phone = ""
    address = scraped.get("address", "")
    city = scraped.get("city", "")
    state = scraped.get("state", "")
    country = scraped.get("country", "India")
    role = scraped.get("company_role", default_role)
    industry = scraped.get("industry", "Industrial Manufacturing & Supply")
    description = scraped.get("description", "")

    if not ext_company:
        ext_company = ExternalCompany.objects.create(
            name=name[:200],
            domain=domain_key[:150],
            website=website[:255],
            email=email[:254],
            phone=phone[:50],
            address=address[:300],
            city=city[:100],
            state=state[:100],
            country=country[:100],
            company_role=role,
            industry=industry[:150],
            description=description[:600],
            source_urls=[cand.get("url", "")] if cand.get("url") else [],
            last_scraped_at=timezone.now(),
        )
    else:
        updated = False
        # Overwrite legacy dummy phones or update with newly discovered phone
        if ext_company.phone in DUMMY_PHONES or (phone and phone != ext_company.phone):
            ext_company.phone = phone[:50]
            updated = True

        # Overwrite legacy dummy emails or update with newly discovered email
        if not ext_company.email or (email and email != ext_company.email):
            ext_company.email = email[:254]
            updated = True

        if address and not ext_company.address:
            ext_company.address = address[:300]
            updated = True
        if city and (not ext_company.city or ext_company.city == "Mumbai"):
            ext_company.city = city[:100]
            updated = True
        if state and (not ext_company.state or ext_company.state == "Maharashtra"):
            ext_company.state = state[:100]
            updated = True
        if name and ext_company.name in ["Industrial Buyer", "Supplier", "Enterprise Partner"]:
            ext_company.name = name[:200]
            updated = True
        if cand.get("url") and cand["url"] not in ext_company.source_urls:
            ext_company.source_urls.append(cand["url"])
            updated = True

        if updated:
            ext_company.last_scraped_at = timezone.now()
            ext_company.save()

    return ext_company


def run_find_buyers_search(product: Product, user, company, criteria_override: list | None = None) -> SearchJob:
    """
    High-Performance Find Buyers Pipeline (Parallel Concurrency):
    1. Formulates search query based on product name, category, and target location.
    2. Runs WebSearchProvider to retrieve candidate B2B companies / procurement notices.
    3. Crawls company websites AND evaluates Gemini AI matches concurrently via ThreadPoolExecutor
       applying the company's active dynamic Matching Parameters or custom criteria override.
    4. Saves/updates ExternalCompany and SearchResult (type LEAD) in MySQL.
    """
    category_name = product.category.name if product.category else ""
    location = product.location or (company.city if company else "India")
    query = f"{product.name} {category_name} industrial buyers procurement rfq {location}".strip()

    # Credit enforcement & atomic reservation via CreditWalletService
    usage_rec = None
    if company:
        ok, msg, usage_rec = CreditWalletService.reserve_credits(
            company=company,
            user=user,
            feature_code="vendor_discovery",
            quantity=1,
            idempotency_key=f"buyer_search:{company.id}:{user.id if user else 0}:{product.id}:{timezone.now().strftime('%Y%m%d%H%M%S')}",
        )
        if not ok:
            return SearchJob.objects.create(
                company=company,
                user=user,
                job_type=SearchJob.JobType.FIND_BUYERS,
                product=product,
                search_query=query,
                status=SearchJob.Status.FAILED,
                error_message=msg,
                started_at=timezone.now(),
                finished_at=timezone.now(),
            )

    job = SearchJob.objects.create(
        company=company,
        user=user,
        job_type=SearchJob.JobType.FIND_BUYERS,
        product=product,
        search_query=query,
        status=SearchJob.Status.RUNNING,
        progress_percent=20,
        started_at=timezone.now(),
    )

    try:
        # Load active matching criteria for this company or use criteria_override
        if criteria_override is not None:
            active_params = criteria_override
        else:
            matching_params = ensure_default_parameters_for_company(company)
            active_params = [p for p in matching_params if p.is_active]

        search_provider = WebSearchProvider(timeout=3.5)
        candidates = search_provider.search(query, num_results=6)
        job.progress_percent = 40
        job.save(update_fields=["progress_percent"])

        # Concurrent parallel processing for scraping & evaluations
        processed_items = []
        max_workers = min(len(candidates), 6) if candidates else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_cand = {
                executor.submit(_process_buyer_candidate, cand, product, active_params): cand
                for cand in candidates
            }
            for future in as_completed(future_to_cand):
                try:
                    res = future.result()
                    processed_items.append(res)
                except Exception as ex:
                    logger.warning(f"Error processing candidate in thread: {ex}")

        job.progress_percent = 80
        job.save(update_fields=["progress_percent"])

        saved_results = 0
        seen_ext_company_ids = set()
        for cand, scraped, fit in processed_items:
            ext_company = _save_or_update_external_company(cand, scraped, default_role="end_user")
            if ext_company.id in seen_ext_company_ids:
                continue
            seen_ext_company_ids.add(ext_company.id)

            combined_raw = {
                **scraped,
                "matched_parameters": fit.get("matched_parameters", []),
            }

            SearchResult.objects.create(
                search_job=job,
                external_company=ext_company,
                result_type=SearchResult.ResultType.LEAD,
                product_title=product.name,
                price=fit.get("price"),
                price_currency=fit.get("price_currency", "INR"),
                moq=fit.get("moq", ""),
                match_score=fit.get("match_score", 85),
                match_reason=fit.get("match_reason", ""),
                need_signal=fit.get("need_signal", ""),
                source_url=cand.get("url", "")[:500],
                raw_data=combined_raw,
            )
            saved_results += 1

        job.status = SearchJob.Status.COMPLETED
        job.total_results = saved_results
        job.progress_percent = 100
        job.finished_at = timezone.now()
        job.save()

        # Commit AI search credit deduction
        if usage_rec:
            usage_rec.search_job = job
            usage_rec.save(update_fields=["search_job"])
            CreditWalletService.commit_usage(usage_rec)

        APILog.objects.create(
            provider="WebSearchProvider & Scraper (Parallel)",
            endpoint="run_find_buyers_search",
            status_code=200,
            search_job=job,
        )

    except Exception as e:
        logger.exception(f"Error during find_buyers_search: {e}")
        if usage_rec:
            CreditWalletService.refund_or_release(usage_rec, reason=str(e))
        job.status = SearchJob.Status.FAILED
        job.error_message = str(e)
        job.finished_at = timezone.now()
        job.save()

    return job


def run_find_suppliers_search(requirement: Requirement, user, company) -> SearchJob:
    """
    High-Performance Find Suppliers Pipeline (Parallel Concurrency):
    1. Formulates search query based on requirement item name, specs, and destination.
    2. Runs WebSearchProvider to retrieve candidate verified manufacturers & exporters.
    3. Crawls websites AND evaluates Gemini AI matches concurrently via ThreadPoolExecutor.
    4. Saves/updates ExternalCompany and SearchResult (type SUPPLIER) in MySQL.
    """
    category_name = requirement.category.name if requirement.category else ""
    country = requirement.delivery_country or "India"
    query = f"{requirement.item_name} {category_name} manufacturers suppliers exporters {country}".strip()

    # Credit enforcement & atomic reservation via CreditWalletService
    usage_rec = None
    if company:
        ok, msg, usage_rec = CreditWalletService.reserve_credits(
            company=company,
            user=user,
            feature_code="vendor_discovery",
            quantity=1,
            idempotency_key=f"supplier_search:{company.id}:{user.id if user else 0}:{requirement.id}:{timezone.now().strftime('%Y%m%d%H%M%S')}",
        )
        if not ok:
            return SearchJob.objects.create(
                company=company,
                user=user,
                job_type=SearchJob.JobType.FIND_SUPPLIERS,
                requirement=requirement,
                search_query=query,
                status=SearchJob.Status.FAILED,
                error_message=msg,
                started_at=timezone.now(),
                finished_at=timezone.now(),
            )

    job = SearchJob.objects.create(
        company=company,
        user=user,
        job_type=SearchJob.JobType.FIND_SUPPLIERS,
        requirement=requirement,
        search_query=query,
        status=SearchJob.Status.RUNNING,
        progress_percent=20,
        started_at=timezone.now(),
    )

    try:
        search_provider = WebSearchProvider(timeout=3.5)
        candidates = search_provider.search(query, num_results=6)
        job.progress_percent = 40
        job.save(update_fields=["progress_percent"])

        # Concurrent parallel processing
        processed_items = []
        max_workers = min(len(candidates), 6) if candidates else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_cand = {
                executor.submit(_process_supplier_candidate, cand, requirement): cand
                for cand in candidates
            }
            for future in as_completed(future_to_cand):
                try:
                    res = future.result()
                    processed_items.append(res)
                except Exception as ex:
                    logger.warning(f"Error processing candidate in thread: {ex}")

        job.progress_percent = 80
        job.save(update_fields=["progress_percent"])

        saved_results = 0
        seen_ext_company_ids = set()
        for cand, scraped, fit in processed_items:
            ext_company = _save_or_update_external_company(cand, scraped, default_role="supplier")
            if ext_company.id in seen_ext_company_ids:
                continue
            seen_ext_company_ids.add(ext_company.id)

            SearchResult.objects.create(
                search_job=job,
                external_company=ext_company,
                result_type=SearchResult.ResultType.SUPPLIER,
                product_title=requirement.item_name,
                price=fit.get("price"),
                price_currency=fit.get("price_currency", "INR"),
                price_unit=requirement.unit or "pcs",
                moq=fit.get("moq", ""),
                is_global_cheaper=fit.get("is_global_cheaper", False),
                savings_percent=fit.get("savings_percent"),
                match_score=fit.get("match_score", 85),
                match_reason=fit.get("match_reason", ""),
                need_signal=fit.get("need_signal", ""),
                source_url=cand.get("url", "")[:500],
                raw_data=scraped,
            )
            saved_results += 1

        job.status = SearchJob.Status.COMPLETED
        job.total_results = saved_results
        job.progress_percent = 100
        job.finished_at = timezone.now()
        job.save()

        if requirement.status == Requirement.Status.DRAFT:
            requirement.status = Requirement.Status.SEARCHING
            requirement.save(update_fields=["status"])

        # Commit AI search credit deduction
        if usage_rec:
            usage_rec.search_job = job
            usage_rec.save(update_fields=["search_job"])
            CreditWalletService.commit_usage(usage_rec)

        APILog.objects.create(
            provider="WebSearchProvider & Scraper (Parallel)",
            endpoint="run_find_suppliers_search",
            status_code=200,
            search_job=job,
        )

    except Exception as e:
        logger.exception(f"Error during find_suppliers_search: {e}")
        if usage_rec:
            CreditWalletService.refund_or_release(usage_rec, reason=str(e))
        job.status = SearchJob.Status.FAILED
        job.error_message = str(e)
        job.finished_at = timezone.now()
        job.save()

    return job
