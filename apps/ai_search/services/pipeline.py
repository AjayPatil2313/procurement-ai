import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.utils import timezone
from apps.ai_search.models import SearchJob, SearchResult, ExternalCompany, APILog
from apps.ai_search.services.search_provider import WebSearchProvider
from apps.ai_search.services.web_scraper import CompanyWebScraper
from apps.ai_search.services.ai_matcher import AIMatcher
from apps.catalog.models import Product
from apps.requirements.models import Requirement

logger = logging.getLogger(__name__)


import urllib.parse

def _get_or_scrape_company(cand: dict) -> dict:
    """Returns cached company data if available in DB, else scrapes target website."""
    url = cand.get("url", "")
    domain = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    if domain:
        cached = ExternalCompany.objects.filter(domain=domain).first()
        if cached and (cached.email or cached.phone) and cached.name:
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

    scraper = CompanyWebScraper(timeout=3.0)
    return scraper.scrape(
        url=url,
        snippet_title=cand.get("title", ""),
        snippet_text=cand.get("snippet", ""),
    )


def _process_buyer_candidate(cand: dict, product: Product) -> tuple[dict, dict, dict]:
    """Scrapes company (with cache check) and runs AI fit evaluation in parallel worker thread."""
    scraped = _get_or_scrape_company(cand)
    fit = AIMatcher.evaluate_fit(
        scraped_company=scraped,
        target_item_name=product.name,
        target_specs=product.specifications or "",
        target_price=product.price,
        currency=product.currency or "INR",
        job_type="find_buyers",
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


def run_find_buyers_search(product: Product, user, company) -> SearchJob:
    """
    High-Performance Find Buyers Pipeline (Parallel Concurrency):
    1. Formulates search query based on product name, category, and target location.
    2. Runs WebSearchProvider to retrieve candidate B2B companies / procurement notices.
    3. Crawls company websites AND evaluates Gemini AI matches concurrently via ThreadPoolExecutor.
    4. Saves/updates ExternalCompany and SearchResult (type LEAD) in MySQL.
    """
    category_name = product.category.name if product.category else ""
    location = product.location or (company.city if company else "India")
    query = f"{product.name} {category_name} industrial buyers procurement rfq {location}".strip()

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
        search_provider = WebSearchProvider(timeout=1.8)
        candidates = search_provider.search(query, num_results=6)
        job.progress_percent = 40
        job.save(update_fields=["progress_percent"])

        # Concurrent parallel processing for scraping & Gemini evaluations
        processed_items = []
        max_workers = min(len(candidates), 6) if candidates else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_cand = {
                executor.submit(_process_buyer_candidate, cand, product): cand
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
        for cand, scraped, fit in processed_items:
            domain_key = scraped.get("domain") or cand.get("url", "")
            ext_company = ExternalCompany.objects.filter(domain=domain_key).first()

            if not ext_company:
                ext_company = ExternalCompany.objects.create(
                    name=scraped.get("name", cand.get("title", "Industrial Buyer")),
                    domain=domain_key[:150],
                    website=scraped.get("website", cand.get("url", ""))[:255],
                    email=scraped.get("email", "")[:254],
                    phone=scraped.get("phone", "")[:50],
                    address=scraped.get("address", "")[:300],
                    city=scraped.get("city", "")[:100],
                    state=scraped.get("state", "")[:100],
                    country=scraped.get("country", "")[:100],
                    company_role=scraped.get("company_role", "end_user"),
                    industry=scraped.get("industry", "")[:150],
                    description=scraped.get("description", "")[:600],
                    source_urls=[cand.get("url", "")],
                    last_scraped_at=timezone.now(),
                )
            else:
                updated = False
                if not ext_company.email and scraped.get("email"):
                    ext_company.email = scraped["email"][:254]
                    updated = True
                if not ext_company.phone and scraped.get("phone"):
                    ext_company.phone = scraped["phone"][:50]
                    updated = True
                if cand.get("url") and cand["url"] not in ext_company.source_urls:
                    ext_company.source_urls.append(cand["url"])
                    updated = True
                if updated:
                    ext_company.last_scraped_at = timezone.now()
                    ext_company.save()

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
                raw_data=scraped,
            )
            saved_results += 1

        job.status = SearchJob.Status.COMPLETED
        job.total_results = saved_results
        job.progress_percent = 100
        job.finished_at = timezone.now()
        job.save()

        APILog.objects.create(
            provider="WebSearchProvider & Scraper (Parallel)",
            endpoint="run_find_buyers_search",
            status_code=200,
            search_job=job,
        )

    except Exception as e:
        logger.exception(f"Error during find_buyers_search: {e}")
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
        search_provider = WebSearchProvider(timeout=1.8)
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
        for cand, scraped, fit in processed_items:
            domain_key = scraped.get("domain") or cand.get("url", "")
            ext_company = ExternalCompany.objects.filter(domain=domain_key).first()

            if not ext_company:
                ext_company = ExternalCompany.objects.create(
                    name=scraped.get("name", cand.get("title", "Supplier")),
                    domain=domain_key[:150],
                    website=scraped.get("website", cand.get("url", ""))[:255],
                    email=scraped.get("email", "")[:254],
                    phone=scraped.get("phone", "")[:50],
                    address=scraped.get("address", "")[:300],
                    city=scraped.get("city", "")[:100],
                    state=scraped.get("state", "")[:100],
                    country=scraped.get("country", "")[:100],
                    company_role=scraped.get("company_role", "supplier"),
                    industry=scraped.get("industry", "")[:150],
                    description=scraped.get("description", "")[:600],
                    source_urls=[cand.get("url", "")],
                    last_scraped_at=timezone.now(),
                )
            else:
                updated = False
                if not ext_company.email and scraped.get("email"):
                    ext_company.email = scraped["email"][:254]
                    updated = True
                if not ext_company.phone and scraped.get("phone"):
                    ext_company.phone = scraped["phone"][:50]
                    updated = True
                if cand.get("url") and cand["url"] not in ext_company.source_urls:
                    ext_company.source_urls.append(cand["url"])
                    updated = True
                if updated:
                    ext_company.last_scraped_at = timezone.now()
                    ext_company.save()

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

        APILog.objects.create(
            provider="WebSearchProvider & Scraper (Parallel)",
            endpoint="run_find_suppliers_search",
            status_code=200,
            search_job=job,
        )

    except Exception as e:
        logger.exception(f"Error during find_suppliers_search: {e}")
        job.status = SearchJob.Status.FAILED
        job.error_message = str(e)
        job.finished_at = timezone.now()
        job.save()

    return job
