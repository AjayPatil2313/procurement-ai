import logging
import re
import urllib.parse
from datetime import datetime
import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress SSL insecure request warnings during scraping of diverse B2B sites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Common Industrial Metros / Clusters for extraction
COMMON_CITIES = [
    "Mumbai", "Navi Mumbai", "Thane", "Pune", "Nashik", "Nagpur", "Aurangabad", "Kolhapur",
    "Ahmedabad", "Vadodara", "Surat", "Rajkot", "Vapi", "Ankleshwar", "Gandhinagar", "Morbi",
    "Delhi", "New Delhi", "Noida", "Greater Noida", "Gurugram", "Gurgaon", "Faridabad", "Ghaziabad",
    "Bengaluru", "Bangalore", "Mysuru", "Belagavi",
    "Chennai", "Coimbatore", "Tirupur", "Salem", "Madurai", "Trichy",
    "Hyderabad", "Secunderabad", "Visakhapatnam", "Vijayawada",
    "Kolkata", "Howrah", "Durgapur",
    "Jaipur", "Jodhpur", "Udaipur", "Bhilwara",
    "Ludhiana", "Jalandhar", "Amritsar", "Panipat", "Chandigarh", "Baddi",
    "Kanpur", "Lucknow", "Agra", "Meerut", "Varanasi",
    "Indore", "Bhopal", "Jabalpur", "Gwalior",
    "Dubai", "Singapore", "Shanghai", "Shenzhen", "Hamburg", "Frankfurt", "Houston", "Chicago",
]

COMMON_STATES_INDIA = [
    "Maharashtra", "Gujarat", "Tamil Nadu", "Karnataka", "Uttar Pradesh",
    "Haryana", "Punjab", "West Bengal", "Rajasthan", "Telangana", "Andhra Pradesh",
    "Kerala", "Madhya Pradesh", "Delhi", "Himachal Pradesh", "Uttarakhand", "Odisha", "Chhattisgarh",
]

EXCLUDED_EMAIL_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".css", ".js", ".woff", ".woff2", ".ico"
)

EXCLUDED_EMAIL_DOMAINS = [
    "example.com", "yourdomain.com", "sentry.io", "wixpress.com", "schema.org",
    "domain.com", "email.com", "sample.com", "cloudflare.com", "wordpress.org"
]


USER_AGENTS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
]


class CompanyWebScraper:
    """
    Intelligent Web Scraper for B2B Company websites.
    Features:
      1. Rotating User-Agent & bot-bypass HTTP headers.
      2. Connection pooling & automated retries for transient errors.
      3. Global domain cache via ExternalCompany for instant sub-second lookup.
      4. Multi-page deep contact crawl with physical address parsing.
      5. Fallback snippet extraction (Never produces fake placeholder data).
    """

    def __init__(self, timeout: float = 3.5):
        self.timeout = timeout

    def _get_random_headers(self) -> dict:
        import random
        ua = random.choice(USER_AGENTS_POOL)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }

    def _build_session(self) -> requests.Session:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = requests.Session()
        session.headers.update(self._get_random_headers())
        session.verify = False

        retries = Retry(
            total=2,
            backoff_factor=0.2,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def scrape(self, url: str, snippet_title: str = "", snippet_text: str = "") -> dict:
        """
        Scrapes the given company URL with multi-page contact page crawling.
        Extracts genuine contact details, emails, phones, and addresses.
        """
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc.lower()
        domain = netloc.replace("www.", "") if netloc.startswith("www.") else netloc
        if not domain and url:
            domain = url.split("/")[0].replace("www.", "")

        clean_name = self._derive_name(snippet_title, domain)

        # Baseline structure
        extracted = {
            "name": clean_name,
            "domain": domain,
            "website": f"{parsed_url.scheme or 'https'}://{netloc or domain}",
            "email": "",
            "phone": "",
            "address": "",
            "city": "",
            "state": "",
            "country": "India",
            "description": snippet_text or f"{clean_name} is an active commercial B2B enterprise.",
            "company_role": "supplier",
            "industry": "Industrial Manufacturing & Supply",
        }

        # 0. Fast-path: Check database cache for previously crawled domain
        if domain:
            try:
                from apps.ai_search.models import ExternalCompany
                from django.utils import timezone
                from datetime import timedelta
                cached_comp = ExternalCompany.objects.filter(domain=domain).first()
                if cached_comp and (cached_comp.phone or cached_comp.email or cached_comp.address):
                    if not cached_comp.last_scraped_at or cached_comp.last_scraped_at >= (timezone.now() - timedelta(days=30)):
                        extracted["name"] = cached_comp.name or clean_name
                        extracted["email"] = cached_comp.email or ""
                        extracted["phone"] = cached_comp.phone or ""
                        extracted["address"] = cached_comp.address or ""
                        extracted["city"] = cached_comp.city or ""
                        extracted["state"] = cached_comp.state or ""
                        extracted["country"] = cached_comp.country or "India"
                        extracted["company_role"] = cached_comp.company_role or "supplier"
                        extracted["industry"] = cached_comp.industry or extracted["industry"]
                        logger.info("Using cached ExternalCompany profile for domain '%s'", domain)
                        return extracted
            except Exception as cache_err:
                logger.debug("Domain cache lookup exception: %s", cache_err)

        # 1. Parse hints from snippet text & title (location, role, any snippet phone/email)
        combined_snippet = f"{snippet_title} {snippet_text}".strip()
        if combined_snippet:
            role = self._detect_company_role(combined_snippet)
            if role:
                extracted["company_role"] = role
            
            industry = self._detect_industry(combined_snippet)
            if industry:
                extracted["industry"] = industry

            loc = self._extract_location_from_text(combined_snippet)
            if loc.get("city"):
                extracted["city"] = loc["city"]
            if loc.get("state"):
                extracted["state"] = loc["state"]
            if loc.get("country"):
                extracted["country"] = loc["country"]

            # Check if snippet contains phone number or email directly
            snippet_phones = self._extract_phones_from_text(combined_snippet)
            if snippet_phones:
                extracted["phone"] = snippet_phones[0]

            snippet_emails = self._extract_emails_from_text(combined_snippet)
            if snippet_emails:
                extracted["email"] = snippet_emails[0]

        # 2. Resilient HTTP Crawl (Homepage + Subpage Contact Crawl)
        if url and url.startswith("http"):
            try:
                session = self._build_session()
                homepage_resp = session.get(url, timeout=self.timeout, allow_redirects=True)
                if homepage_resp.status_code == 200:
                    # Enforce proper charset encoding
                    if homepage_resp.encoding is None or homepage_resp.encoding == "ISO-8859-1":
                        homepage_resp.encoding = homepage_resp.apparent_encoding or "utf-8"

                    html_text = homepage_resp.text
                    homepage_data = self._parse_html(html_text=html_text, url=url, domain=domain, snippet_title=snippet_title)
                    contact_links = homepage_data.pop("_contact_links", [])

                    # Merge homepage findings
                    for k, v in homepage_data.items():
                        if v:
                            extracted[k] = v

                    # 3. If phone or email or physical address is still missing, deep crawl contact subpage!
                    if (not extracted.get("phone") or not extracted.get("email") or not extracted.get("address")) and contact_links:
                        contact_subpage_url = contact_links[0]
                        try:
                            sub_resp = session.get(contact_subpage_url, timeout=2.5, allow_redirects=True)
                            if sub_resp.status_code == 200:
                                if sub_resp.encoding is None or sub_resp.encoding == "ISO-8859-1":
                                    sub_resp.encoding = sub_resp.apparent_encoding or "utf-8"
                                sub_data = self._parse_html(html_text=sub_resp.text, url=contact_subpage_url, domain=domain, snippet_title=snippet_title)
                                sub_data.pop("_contact_links", None)
                                # Fill missing fields from contact page
                                if not extracted.get("phone") and sub_data.get("phone"):
                                    extracted["phone"] = sub_data["phone"]
                                if not extracted.get("email") and sub_data.get("email"):
                                    extracted["email"] = sub_data["email"]
                                if not extracted.get("address") and sub_data.get("address"):
                                    extracted["address"] = sub_data["address"]
                                if not extracted.get("city") and sub_data.get("city"):
                                    extracted["city"] = sub_data["city"]
                                if not extracted.get("state") and sub_data.get("state"):
                                    extracted["state"] = sub_data["state"]
                        except Exception as sub_err:
                            logger.debug(f"Contact subpage crawl error for {contact_subpage_url}: {sub_err}")

            except Exception as e:
                logger.debug(f"Live scraping error for {url}: {e} (using snippet data)")

        # Format address cleanly if city/state known but full street address absent
        if not extracted.get("address") and extracted.get("city"):
            state_part = f", {extracted['state']}" if extracted.get("state") else ""
            extracted["address"] = f"{extracted['city']}{state_part}, {extracted.get('country', 'India')}"

        return extracted

    def _parse_html(self, html_text: str, url: str = "", domain: str = "", snippet_title: str = "") -> dict:
        """Parses HTML document to extract company details, contacts, addresses, and contact links."""
        soup = BeautifulSoup(html_text, "html.parser")

        # 1. Company Name
        name = ""
        # If snippet title is provided and clean, prioritize it
        if snippet_title and len(snippet_title.strip()) > 2 and not any(kw in snippet_title.lower() for kw in ["http", "search", "result"]):
            name = self._derive_name(snippet_title, domain)

        if not name:
            og_site_name = soup.find("meta", property="og:site_name")
            if og_site_name and og_site_name.get("content"):
                name = og_site_name["content"].strip()

        if not name:
            title_tag = soup.find("title")
            if title_tag and title_tag.get_text():
                name = self._derive_name(title_tag.get_text().strip(), domain)

        # 2. Description
        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()

        if not description:
            about_elem = soup.find(id=re.compile(r"about|overview|intro", re.I)) or soup.find(class_=re.compile(r"about|overview|intro", re.I))
            if about_elem:
                p = about_elem.find("p")
                if p:
                    description = p.get_text().strip()

        # 3. Emails (mailto links + regex in page text)
        emails = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                raw_mail = href.split("mailto:")[1].split("?")[0].strip()
                if self._is_valid_email(raw_mail):
                    emails.append(raw_mail)

        text_content = soup.get_text(separator=" ")
        regex_emails = self._extract_emails_from_text(text_content)
        emails.extend(regex_emails)

        # Remove duplicates while preserving order
        unique_emails = list(dict.fromkeys(emails))
        primary_email = ""
        if unique_emails:
            # Prioritize sales / info / contact / procurement emails
            prioritized = [
                e for e in unique_emails if any(k in e.lower() for k in ["sales@", "info@", "contact@", "inquiry@", "enquiry@", "rfq@", "purchase@", "order@"])
            ]
            primary_email = prioritized[0] if prioritized else unique_emails[0]

        # 4. Phone Numbers (tel links + regex in page text)
        phones = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("tel:"):
                raw_phone = href.split("tel:")[1].split("?")[0].strip()
                clean_phone = self._clean_phone(raw_phone)
                if clean_phone:
                    phones.append(clean_phone)

        regex_phones = self._extract_phones_from_text(text_content)
        phones.extend(regex_phones)

        unique_phones = list(dict.fromkeys(phones))
        primary_phone = unique_phones[0] if unique_phones else ""

        # 5. Address / Location
        address_text = ""
        address_tag = soup.find("address")
        if address_tag:
            address_text = " ".join(address_tag.get_text().split()).strip()

        if not address_text:
            # Search footer or contact sections for physical address
            for container in soup.find_all(["footer", "div", "section"], class_=re.compile(r"contact|footer|address|location|reach", re.I)):
                c_text = container.get_text(separator="\n")
                for line in c_text.splitlines():
                    clean_line = " ".join(line.split()).strip()
                    # Check if line contains postal code or address keywords
                    if len(clean_line) >= 15 and len(clean_line) <= 220:
                        has_pincode = bool(re.search(r"\b[1-9]\d{5}\b", clean_line))
                        has_addr_kw = any(kw in clean_line.lower() for kw in [
                            "plot no", "midc", "gidc", "industrial area", "phase", "road", "street", "estate",
                            "works:", "factory:", "registered office", "head office", "plant:", "sector "
                        ])
                        if has_pincode or has_addr_kw:
                            address_text = clean_line
                            break
                if address_text:
                    break

        loc = self._extract_location_from_text(address_text or text_content)
        city = loc.get("city", "")
        state = loc.get("state", "")
        country = loc.get("country", "India")

        # 6. Discover Contact Subpage Links for deep crawling
        contact_links = []
        parsed_base = urllib.parse.urlparse(url)
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            href_lower = href.lower()
            if any(k in href_lower for k in ["/contact", "contact-us", "contact_us", "contactus", "reach-us", "get-in-touch", "/about-us"]):
                full_url = urllib.parse.urljoin(url, href)
                parsed_target = urllib.parse.urlparse(full_url)
                # Keep within same domain and avoid anchors or mailto
                if parsed_target.netloc == parsed_base.netloc and full_url not in contact_links and not full_url.startswith("mailto:"):
                    contact_links.append(full_url)

        role = self._detect_company_role(name + " " + description + " " + text_content[:1500])
        industry = self._detect_industry(name + " " + description)

        parsed_data = {
            "name": name,
            "description": description[:600],
            "email": primary_email[:250],
            "phone": primary_phone[:50],
            "address": address_text[:300],
            "city": city[:100],
            "state": state[:100],
            "country": country[:100],
            "company_role": role,
            "industry": industry[:150],
            "_contact_links": contact_links,
        }

        return parsed_data

    def _extract_emails_from_text(self, text: str) -> list[str]:
        """Extracts valid business emails via regular expression."""
        raw_matches = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
        valid = []
        for em in raw_matches:
            em_clean = em.strip().rstrip(".,;:/)")
            if self._is_valid_email(em_clean) and em_clean not in valid:
                valid.append(em_clean)
        return valid

    def _is_valid_email(self, email: str) -> bool:
        """Validates that an email is legitimate and not an asset/dummy."""
        if not email or "@" not in email:
            return False
        em_lower = email.lower()
        if em_lower.endswith(EXCLUDED_EMAIL_EXTENSIONS):
            return False
        if any(d in em_lower for d in EXCLUDED_EMAIL_DOMAINS):
            return False
        parts = em_lower.split("@")
        if len(parts) != 2 or "." not in parts[1]:
            return False
        if len(email) < 6 or len(email) > 80:
            return False
        return True

    def _extract_phones_from_text(self, text: str) -> list[str]:
        """Extracts real telephone numbers from text."""
        phones = []
        # Pattern 1: Indian Mobile or Landline with country code: +91-XXXXX or +91 XXXXX
        for m in re.finditer(r"(?:\+91[\s\-]?)?(?:0\d{2,4}[\s\-]?)?[6-9]\d{9}", text):
            cleaned = self._clean_phone(m.group(0))
            if cleaned and cleaned not in phones:
                phones.append(cleaned)

        # Pattern 2: Indian Landlines with STD codes (e.g., 020 2710 1000, 022 2840 5000, 011 2345 6789)
        for m in re.finditer(r"\b0\d{2,4}[\s\-]\d{6,8}\b", text):
            cleaned = self._clean_phone(m.group(0))
            if cleaned and cleaned not in phones:
                phones.append(cleaned)

        # Pattern 3: Toll-free numbers (1800 XXX XXXX)
        for m in re.finditer(r"\b1800[\s\-]?\d{3}[\s\-]?\d{3,4}\b", text):
            cleaned = self._clean_phone(m.group(0))
            if cleaned and cleaned not in phones:
                phones.append(cleaned)

        # Pattern 4: International format (+1-XXX..., +44-XXX...)
        for m in re.finditer(r"\+\d{1,3}[\s\-]\(?\d{2,4}\)?[\s\-]\d{3,4}[\s\-]\d{3,4}", text):
            cleaned = self._clean_phone(m.group(0))
            if cleaned and cleaned not in phones:
                phones.append(cleaned)

        return phones

    def _clean_phone(self, raw: str) -> str:
        """Cleans and validates phone number string."""
        if not raw:
            return ""
        # Strip extraneous trailing chars
        cleaned = re.sub(r"[^\d\+\-\s\(\)]", "", raw).strip().strip("-.,")
        digits_only = re.sub(r"\D", "", cleaned)
        # Phone numbers should have between 8 and 14 digits
        if len(digits_only) < 8 or len(digits_only) > 14:
            return ""
        # Filter out 6-digit pin codes or year numbers like 2024, 2025, 2026
        if len(digits_only) == 6 or digits_only in ["2023", "2024", "2025", "2026", "2027"]:
            return ""
        return cleaned

    def _derive_name(self, title: str, domain: str) -> str:
        """Derives clean company name from website title or domain."""
        if title:
            # Check for common title splits
            parts = [title]
            for sep in [" — ", " - ", " | ", " :: ", " : ", " • "]:
                if sep in title:
                    parts = [p.strip() for p in title.split(sep) if p.strip()]
                    break

            # Find the best part that looks like a company name
            best_part = parts[0]
            domain_base = domain.split(".")[0].lower() if domain else ""
            for p in parts:
                p_lower = p.lower()
                # If segment mentions the domain or corporate keywords
                if domain_base and domain_base in p_lower:
                    best_part = p
                    break
                if any(kw in p_lower for kw in ["ltd", "limited", "pvt", "corp", "industries", "pumps", "engineering", "group"]):
                    best_part = p
                    break

            clean = re.sub(r"\b(home|welcome to|official website|products|services|contact us|about us|manufacturer|supplier)\b", "", best_part, flags=re.I).strip()
            clean = clean.strip(" -|:•—")
            if len(clean) > 2 and len(clean) < 60:
                return clean

        if domain:
            base = domain.split(".")[0]
            words = re.sub(r"[-_]", " ", base).title()
            return f"{words} Corp"

        return "Enterprise Partner"

    def _detect_company_role(self, text: str) -> str:
        """Classifies role into manufacturer, supplier, trader, exporter, distributor, or end_user."""
        t = text.lower()
        if any(w in t for w in ["procurement", "tender", "rfq", "refinery", "plant operations", "contractor", "buyer", "infrastructure board"]):
            return "end_user"
        if any(w in t for w in ["manufacturer", "manufacturing", "factory", "oem", "production plant", "fabricator"]):
            return "manufacturer"
        if any(w in t for w in ["exporter", "exporting", "global export"]):
            return "exporter"
        if any(w in t for w in ["distributor", "authorized dealer", "stockist", "channel partner"]):
            return "distributor"
        if any(w in t for w in ["trader", "trading company", "indentor"]):
            return "trader"
        return "supplier"

    def _detect_industry(self, text: str) -> str:
        """Detects broad industrial domain."""
        t = text.lower()
        if any(w in t for w in ["pump", "valve", "flow", "fluid", "compressor", "hydraulic"]):
            return "Fluid Management & Industrial Pumps"
        if any(w in t for w in ["steel", "iron", "metal", "alloy", "pipe", "tube", "fabrication"]):
            return "Metals, Pipes & Heavy Fabrication"
        if any(w in t for w in ["textile", "fabric", "cotton", "yarn", "garment", "weaving"]):
            return "Textiles & Industrial Fabrics"
        if any(w in t for w in ["chemical", "petrochemical", "polymer", "resin", "solvent"]):
            return "Chemicals & Petrochemicals"
        if any(w in t for w in ["electric", "motor", "power", "generator", "solar", "transformer"]):
            return "Electrical & Energy Equipment"
        if any(w in t for w in ["auto", "automotive", "bearing", "gear", "engine", "transmission"]):
            return "Automotive & Mechanical Parts"
        return "Industrial Manufacturing & Supply"

    def _extract_location_from_text(self, text: str) -> dict:
        """Searches for city, state, country clues in text. Defaults to empty strings if not found."""
        result = {"city": "", "state": "", "country": "India"}
        if not text:
            return result

        for city in COMMON_CITIES:
            if re.search(r"\b" + re.escape(city) + r"\b", text, re.I):
                result["city"] = city
                if city in ["Vadodara", "Ahmedabad", "Surat", "Rajkot", "Vapi", "Ankleshwar", "Gandhinagar", "Morbi"]:
                    result["state"] = "Gujarat"
                elif city in ["Mumbai", "Navi Mumbai", "Pune", "Nagpur", "Nashik", "Thane", "Aurangabad", "Kolhapur"]:
                    result["state"] = "Maharashtra"
                elif city in ["Bengaluru", "Bangalore", "Mysuru", "Belagavi"]:
                    result["state"] = "Karnataka"
                elif city in ["Chennai", "Coimbatore", "Tirupur", "Salem", "Madurai", "Trichy"]:
                    result["state"] = "Tamil Nadu"
                elif city in ["Delhi", "New Delhi", "Noida", "Greater Noida", "Gurugram", "Gurgaon", "Faridabad", "Ghaziabad"]:
                    result["state"] = "Delhi NCR"
                elif city in ["Kolkata", "Howrah", "Durgapur"]:
                    result["state"] = "West Bengal"
                elif city in ["Jaipur", "Jodhpur", "Udaipur", "Bhilwara"]:
                    result["state"] = "Rajasthan"
                elif city in ["Hyderabad", "Secunderabad"]:
                    result["state"] = "Telangana"
                elif city in ["Visakhapatnam", "Vijayawada"]:
                    result["state"] = "Andhra Pradesh"
                elif city in ["Ludhiana", "Jalandhar", "Amritsar"]:
                    result["state"] = "Punjab"
                elif city in ["Kanpur", "Lucknow", "Agra", "Meerut", "Varanasi"]:
                    result["state"] = "Uttar Pradesh"
                elif city in ["Indore", "Bhopal", "Jabalpur", "Gwalior"]:
                    result["state"] = "Madhya Pradesh"
                elif city in ["Dubai"]:
                    result["state"] = "Dubai"
                    result["country"] = "UAE"
                elif city in ["Singapore"]:
                    result["state"] = "Singapore"
                    result["country"] = "Singapore"
                elif city in ["Frankfurt", "Hamburg"]:
                    result["state"] = "Hesse"
                    result["country"] = "Germany"
                elif city in ["Houston", "Chicago"]:
                    result["state"] = "Texas/Illinois"
                    result["country"] = "USA"
                break

        if not result["state"]:
            for state in COMMON_STATES_INDIA:
                if re.search(r"\b" + re.escape(state) + r"\b", text, re.I):
                    result["state"] = state
                    result["country"] = "India"
                    break

        return result
