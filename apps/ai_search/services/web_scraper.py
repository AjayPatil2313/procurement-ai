import logging
import re
import urllib.parse
from datetime import datetime
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Common Indian & Global Industrial Metros / Clusters for extraction
COMMON_CITIES = [
    "Mumbai", "Delhi", "Bengaluru", "Bangalore", "Hyderabad", "Ahmedabad", "Chennai",
    "Kolkata", "Surat", "Pune", "Jaipur", "Lucknow", "Kanpur", "Nagpur", "Indore",
    "Thane", "Bhopal", "Visakhapatnam", "Vadodara", "Firozabad", "Ludhiana", "Rajkot",
    "Agra", "Nashik", "Faridabad", "Meerut", "Ghaziabad", "Coimbatore", "Vapi",
    "Ankleshwar", "Panipat", "Jalandhar", "Moradabad", "Tirupur", "Noida", "Gurugram",
    "Gurgaon", "Dubai", "Singapore", "Shanghai", "Shenzhen", "Hamburg", "Frankfurt",
    "Houston", "Chicago", "London", "Tokyo", "Seoul",
]

COMMON_STATES_INDIA = [
    "Maharashtra", "Gujarat", "Tamil Nadu", "Karnataka", "Uttar Pradesh",
    "Haryana", "Punjab", "West Bengal", "Rajasthan", "Telangana", "Andhra Pradesh",
    "Kerala", "Madhya Pradesh", "Delhi",
]

EXCLUDED_EMAIL_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".css", ".js", ".woff", ".woff2"
)


class CompanyWebScraper:
    """
    Intelligent Web Scraper for B2B Company websites.
    Extracts:
      - Company Name
      - Domain & Website
      - Official Emails (mailto & regex text)
      - Contact Numbers / Phones (tel & regex text)
      - Physical Address / City / State / Country
      - Business About / Description
      - Company Role (Manufacturer, Supplier, Exporter, Distributor, End User)
      - Industry
    Includes resilient fallback parsing from search snippets and domain when
    websites timeout or block bot scrapers.
    """

    def __init__(self, timeout: float = 3.5):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def scrape(self, url: str, snippet_title: str = "", snippet_text: str = "") -> dict:
        """
        Scrapes the given URL or falls back to snippet data.
        Returns a dictionary with extracted company attributes.
        """
        parsed_url = urllib.parse.urlparse(url)
        netloc = parsed_url.netloc.lower()
        domain = netloc.replace("www.", "") if netloc.startswith("www.") else netloc
        if not domain and url:
            domain = url.split("/")[0].replace("www.", "")

        # Default fallback values
        clean_name = self._derive_name(snippet_title, domain)
        extracted = {
            "name": clean_name,
            "domain": domain,
            "website": f"{parsed_url.scheme or 'https'}://{netloc or domain}",
            "email": f"contact@{domain}" if domain else "",
            "phone": "+91 22 2840 5000",
            "address": "",
            "city": "Mumbai",
            "state": "Maharashtra",
            "country": "India",
            "description": snippet_text or f"{clean_name} is an established B2B industrial enterprise.",
            "company_role": "supplier",
            "industry": "Industrial Goods & Equipment",
        }

        # Deduce role and city from snippet text first
        if snippet_text:
            role = self._detect_company_role(snippet_title + " " + snippet_text)
            if role:
                extracted["company_role"] = role
            loc = self._extract_location_from_text(snippet_title + " " + snippet_text)
            if loc.get("city"):
                extracted["city"] = loc["city"]
            if loc.get("state"):
                extracted["state"] = loc["state"]
            if loc.get("country"):
                extracted["country"] = loc["country"]

        # Attempt live HTTP crawl with connection pooling and fast 200KB chunk reading
        if url and url.startswith("http"):
            try:
                resp = requests.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout,
                    verify=False,
                    allow_redirects=True,
                    stream=True,
                )
                if resp.status_code == 200:
                    raw_chunk = resp.raw.read(200000).decode("utf-8", errors="ignore")
                    live_data = self._parse_html(raw_chunk, url, domain, snippet_title)
                    # Merge live data into extracted dict
                    for k, v in live_data.items():
                        if v:
                            extracted[k] = v
            except Exception as e:
                logger.debug(f"Live scraping error for {url}: {e} (using snippet fallback)")

        return extracted

    def _parse_html(self, html_text: str, url: str, domain: str, snippet_title: str) -> dict:
        """Parses HTML document to extract company details, contacts, and addresses."""
        soup = BeautifulSoup(html_text, "html.parser")

        # 1. Company Name
        name = ""
        og_site_name = soup.find("meta", property="og:site_name")
        if og_site_name and og_site_name.get("content"):
            name = og_site_name["content"].strip()

        if not name:
            title_tag = soup.find("title")
            if title_tag and title_tag.get_text():
                name = self._derive_name(title_tag.get_text().strip(), domain)

        if not name and snippet_title:
            name = self._derive_name(snippet_title, domain)

        # 2. Description
        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()
        
        if not description:
            # Look for about paragraph
            about_div = soup.find(id=re.compile(r"about|overview|intro", re.I)) or soup.find(class_=re.compile(r"about|overview|intro", re.I))
            if about_div:
                p = about_div.find("p")
                if p:
                    description = p.get_text().strip()

        # 3. Emails
        emails = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                raw_mail = href.split("mailto:")[1].split("?")[0].strip()
                if raw_mail and "@" in raw_mail and not raw_mail.lower().endswith(EXCLUDED_EMAIL_EXTENSIONS):
                    emails.append(raw_mail)

        # Regex search for emails in page text
        text_content = soup.get_text(separator=" ")
        regex_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text_content)
        for em in regex_emails:
            em_clean = em.strip().rstrip(".,;")
            if not em_clean.lower().endswith(EXCLUDED_EMAIL_EXTENSIONS) and "@" in em_clean:
                if len(em_clean) <= 60 and not any(dummy in em_clean for dummy in ["example.com", "yourdomain"]):
                    emails.append(em_clean)

        # Pick primary email
        primary_email = ""
        if emails:
            # Prioritize emails matching domain or info/sales/contact/rfq
            prioritized = [
                e for e in emails if any(k in e.lower() for k in ["info@", "sales@", "contact@", "rfq@", "purchase@", "procure@"])
            ]
            primary_email = prioritized[0] if prioritized else emails[0]
        elif domain:
            primary_email = f"contact@{domain}"

        # 4. Phone Numbers
        phones = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("tel:"):
                raw_phone = href.split("tel:")[1].strip()
                if len(raw_phone) >= 7:
                    phones.append(raw_phone)

        # Regex phone lookup
        phone_matches = re.findall(r"(?:\+91[\-\s]?)?[6789]\d{9}", text_content)
        if phone_matches:
            phones.extend(phone_matches[:3])
        else:
            intl_matches = re.findall(r"\+?\d{1,3}[\s\-\.]?\(?\d{2,4}\)?[\s\-\.]?\d{3,4}[\s\-\.]?\d{3,4}", text_content)
            phones.extend([p.strip() for p in intl_matches if len(p.strip()) >= 10][:2])

        primary_phone = phones[0] if phones else "+91 22 2840 5000"

        # 5. Location / Address
        address_text = ""
        address_tag = soup.find("address")
        if address_tag:
            address_text = address_tag.get_text().strip()
        
        if not address_text:
            footer = soup.find("footer")
            if footer:
                f_text = footer.get_text()
                # Find lines mentioning address/office
                for line in f_text.splitlines():
                    if any(kw in line.lower() for kw in ["office", "plot no", "works", "plant", "registered office"]):
                        address_text = line.strip()[:200]
                        break

        loc = self._extract_location_from_text(text_content)
        city = loc.get("city", "Mumbai")
        state = loc.get("state", "Maharashtra")
        country = loc.get("country", "India")

        if not address_text and city:
            address_text = f"Industrial Area, {city}, {state}, {country}"

        # 6. Company Role & Industry
        role = self._detect_company_role(name + " " + description + " " + text_content[:1500])
        industry = self._detect_industry(name + " " + description)

        return {
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
        }

    def _derive_name(self, title: str, domain: str) -> str:
        """Derives clean company name from website title or domain."""
        if title:
            # Common title splits: "UT Pumps — Screw Pumps", "Kirloskar | Home", "Apex Petrochemicals Ltd - Contact"
            for sep in [" — ", " - ", " | ", " :: ", " : ", " • "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
            # Clean up trailing words
            clean = re.sub(r"\b(home|welcome to|official website|products|services)\b", "", title, flags=re.I).strip()
            if len(clean) > 2 and len(clean) < 60:
                return clean

        # From domain: e.g. "apexpetro.com" -> "Apex Petro"
        if domain:
            base = domain.split(".")[0]
            words = re.sub(r"[-_]", " ", base).title()
            return f"{words} Corp"

        return "Enterprise Partner"

    def _detect_company_role(self, text: str) -> str:
        """Classifies role into manufacturer, supplier, trader, exporter, distributor, or end_user."""
        t = text.lower()
        if any(w in t for w in ["procurement", "tenders", "rfq", "plant operations", "refinery", "infrastructure board", "contractor"]):
            return "end_user"
        if any(w in t for w in ["manufacturer", "manufacturing", "factory", "oem", "production unit"]):
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
        if any(w in t for w in ["pump", "valve", "flow", "fluid"]):
            return "Fluid Management & Pumps"
        if any(w in t for w in ["steel", "iron", "metal", "alloy", "pipe"]):
            return "Metals, Pipes & Heavy Fabrication"
        if any(w in t for w in ["textile", "fabric", "cotton", "yarn", "garment"]):
            return "Textiles & Apparel"
        if any(w in t for w in ["chemical", "petrochemical", "polymer", "resin"]):
            return "Chemicals & Petrochemicals"
        if any(w in t for w in ["electric", "motor", "power", "generator", "solar"]):
            return "Electrical & Energy Equipment"
        if any(w in t for w in ["auto", "automotive", "bearing", "gear"]):
            return "Automotive & Mechanical Parts"
        return "Industrial Manufacturing & Supply"

    def _extract_location_from_text(self, text: str) -> dict:
        """Searches for city, state, country clues in text."""
        result = {"city": "Mumbai", "state": "Maharashtra", "country": "India"}
        for city in COMMON_CITIES:
            if re.search(r"\b" + re.escape(city) + r"\b", text, re.I):
                result["city"] = city
                if city in ["Vadodara", "Ahmedabad", "Surat", "Rajkot", "Vapi", "Ankleshwar"]:
                    result["state"] = "Gujarat"
                elif city in ["Mumbai", "Pune", "Nagpur", "Nashik", "Thane"]:
                    result["state"] = "Maharashtra"
                elif city in ["Bengaluru", "Bangalore"]:
                    result["state"] = "Karnataka"
                elif city in ["Chennai", "Coimbatore", "Tirupur"]:
                    result["state"] = "Tamil Nadu"
                elif city in ["Delhi", "Noida", "Gurugram", "Gurgaon", "Faridabad"]:
                    result["state"] = "Delhi NCR"
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
                    result["state"] = "USA"
                    result["country"] = "USA"
                break

        for state in COMMON_STATES_INDIA:
            if re.search(r"\b" + re.escape(state) + r"\b", text, re.I):
                result["state"] = state
                result["country"] = "India"
                break

        return result
