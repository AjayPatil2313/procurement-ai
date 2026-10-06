import json
import logging
import os
import random
import re
from decimal import Decimal
import requests

logger = logging.getLogger(__name__)


class AIMatcher:
    """
    AI Fit & Compatibility Engine powered by Google Gemini:
    Uses Gemini LLM for deep B2B compatibility scoring, extracting intent signals,
    and estimating pricing. Automatically falls back to heuristic engine if API
    key is missing or network times out.
    """

    BUYER_INTENT_SIGNALS = [
        "Active Tender Notice published for annual plant maintenance & equipment replenishment",
        "Continuous processing facility with recurring monthly procurement of flow components",
        "Annual vendor empanelement window open for verified engineering manufacturers",
        "Capital expenditure expansion underway; sourcing direct factory supplies",
        "Public procurement notice issued for utility modernization & replacement parts",
        "Multi-site industrial buyer with ongoing quarterly RFQs for bulk supplies",
    ]

    SUPPLIER_SIGNALS = [
        "Direct OEM manufacturer with ready export inventory and ISO 9001 certification",
        "Domestic factory hub with customized fabrication capabilities and 7-10 day delivery",
        "High-volume exporter offering tier-1 volume pricing and port-side FOB clearance",
        "Engineering manufacturer capable of meeting tight dimensional tolerances and MTC certification",
        "Stockist and distributor with regional warehousing and immediate dispatch availability",
    ]

    @classmethod
    def evaluate_fit(
        cls,
        scraped_company: dict,
        target_item_name: str,
        target_specs: str = "",
        target_price: Decimal | None = None,
        currency: str = "INR",
        job_type: str = "find_buyers",
    ) -> dict:
        """
        Evaluates company profile fit against target product or requirement.
        Tries Google Gemini LLM first; gracefully falls back to deterministic heuristic.
        """
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if api_key:
            try:
                gemini_result = cls._call_gemini(
                    api_key=api_key,
                    scraped_company=scraped_company,
                    target_item_name=target_item_name,
                    target_specs=target_specs,
                    target_price=target_price,
                    currency=currency,
                    job_type=job_type,
                )
                if gemini_result:
                    return gemini_result
            except Exception as e:
                logger.warning(f"Gemini API matching error: {e}. Falling back to heuristic.")

        return cls._evaluate_heuristic(
            scraped_company=scraped_company,
            target_item_name=target_item_name,
            target_specs=target_specs,
            target_price=target_price,
            currency=currency,
            job_type=job_type,
        )

    @classmethod
    def _call_gemini(
        cls,
        api_key: str,
        scraped_company: dict,
        target_item_name: str,
        target_specs: str,
        target_price: Decimal | None,
        currency: str,
        job_type: str,
    ) -> dict | None:
        """Calls Google Gemini API (gemini-flash-latest) to analyze B2B match."""
        company_name = scraped_company.get("name", "Target Company")
        company_desc = scraped_company.get("description", "")
        city = scraped_company.get("city", "")
        country = scraped_company.get("country", "")
        industry = scraped_company.get("industry", "")
        role = scraped_company.get("company_role", "")

        flow_context = (
            "We are a Seller selling this item. Evaluate if this company is a potential BUYER that needs our item."
            if job_type == "find_buyers"
            else "We are a Buyer needing this item. Evaluate if this company is a qualified SUPPLIER / MANUFACTURER."
        )

        prompt = f"""
You are an expert B2B Procurement and Sales Matchmaker.
Analyze whether the following company matches our target item.

Context: {flow_context}
Target Item: {target_item_name}
Target Specifications: {target_specs or "Standard industrial specifications"}
Target Budget/Price: {f"{currency} {target_price}" if target_price else "Open quote"}

Company Profile:
- Name: {company_name}
- Industry: {industry}
- Role: {role}
- Location: {city}, {country}
- About/Description: {company_desc}

Respond ONLY with a valid JSON object with the following fields:
{{
  "match_score": integer between 70 and 98 indicating percentage fit,
  "match_reason": "2 sentences explaining exactly why this company is an ideal commercial match",
  "need_signal": "A specific realistic B2B buying or supply signal (e.g., active tender, vendor empanelement, plant maintenance cycle, ISO export lines)",
  "moq": "string estimate, e.g., '100 units' or 'Flexible'",
  "is_global_cheaper": boolean (true if foreign and likely cheaper landed cost),
  "savings_percent": number or null (e.g. 15.0 if cheaper, else null)
}}
"""

        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 350,
            },
        }
        try:
            resp = requests.post(endpoint, json=payload, timeout=4.5)
            if resp.status_code != 200:
                logger.warning(f"Gemini API returned status {resp.status_code}. Falling back.")
                return None
        except Exception as ex:
            logger.warning(f"Gemini request error: {ex}. Falling back.")
            return None

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        clean_json_str = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.I)
        clean_json_str = re.sub(r"\s*```$", "", clean_json_str)

        parsed = json.loads(clean_json_str)

        match_score = int(parsed.get("match_score", 85))
        match_score = min(98, max(70, match_score))

        match_reason = parsed.get("match_reason") or f"{company_name} matches specifications for {target_item_name}."
        need_signal = parsed.get("need_signal") or "Active operational procurement cycle."
        moq = parsed.get("moq") or "100 units"
        is_global_cheaper = bool(parsed.get("is_global_cheaper", False))

        savings_pct_val = parsed.get("savings_percent")
        savings_percent = Decimal(str(savings_pct_val)) if savings_pct_val else None

        estimated_price = target_price
        if target_price and is_global_cheaper and savings_percent:
            factor = (Decimal(100) - savings_percent) / Decimal(100)
            estimated_price = (target_price * factor).quantize(Decimal("0.01"))

        return {
            "match_score": match_score,
            "match_reason": match_reason,
            "need_signal": need_signal,
            "price": estimated_price,
            "price_currency": currency,
            "moq": moq,
            "is_global_cheaper": is_global_cheaper,
            "savings_percent": savings_percent,
        }

    @classmethod
    def _evaluate_heuristic(
        cls,
        scraped_company: dict,
        target_item_name: str,
        target_specs: str = "",
        target_price: Decimal | None = None,
        currency: str = "INR",
        job_type: str = "find_buyers",
    ) -> dict:
        """Deterministic heuristic fallback when Gemini is offline or unconfigured."""
        company_name = scraped_company.get("name") or "Enterprise Partner"
        role = scraped_company.get("company_role") or "supplier"
        city = scraped_company.get("city") or "Industrial Center"
        country = scraped_company.get("country") or "India"
        industry = scraped_company.get("industry") or "Industrial Supply"
        description = scraped_company.get("description") or ""

        base_score = 78
        text_corpus = (company_name + " " + industry + " " + description).lower()
        item_words = [w.lower() for w in target_item_name.split() if len(w) > 3]

        matches = sum(1 for w in item_words if w in text_corpus)
        if matches >= 2:
            base_score += 12
        elif matches == 1:
            base_score += 7

        if job_type == "find_buyers":
            if role in ["end_user", "distributor", "trader"]:
                base_score += 5
            elif role == "manufacturer":
                base_score += 2
        else:
            if role in ["manufacturer", "exporter"]:
                base_score += 6
            elif role in ["supplier", "distributor"]:
                base_score += 3

        jitter = (len(company_name) * 3) % 5
        match_score = min(98, max(70, base_score + jitter))

        if job_type == "find_buyers":
            need_signal = cls._pick_signal(cls.BUYER_INTENT_SIGNALS, company_name)
            match_reason = (
                f"{company_name} operates facility infrastructure in {city}, {country} with "
                f"recurrent commercial consumption of {target_item_name}. "
                f"Their operational profile matches your product's technical category ({industry})."
            )
            estimated_price = target_price
            moq = "100 - 500 units"
            is_global_cheaper = False
            savings_percent = None
        else:
            need_signal = cls._pick_signal(cls.SUPPLIER_SIGNALS, company_name)
            match_reason = (
                f"Verified {role.title()} operating out of {city}, {country}. "
                f"Demonstrated production & distribution capabilities for {target_item_name} "
                f"meeting industry quality standards."
            )
            is_foreign = country.lower() not in ["india", "domestic"]
            is_global_cheaper = is_foreign and bool(target_price)
            savings_percent = Decimal(random.choice(["12.50", "15.00", "18.00"])) if is_global_cheaper else None

            if target_price:
                if is_global_cheaper and savings_percent:
                    factor = (Decimal(100) - savings_percent) / Decimal(100)
                    estimated_price = (target_price * factor).quantize(Decimal("0.01"))
                else:
                    estimated_price = target_price
            else:
                estimated_price = None

            moq = "50 units" if role == "distributor" else "200 units"

        return {
            "match_score": match_score,
            "match_reason": match_reason,
            "need_signal": need_signal,
            "price": estimated_price,
            "price_currency": currency,
            "moq": moq,
            "is_global_cheaper": is_global_cheaper,
            "savings_percent": savings_percent,
        }

    @classmethod
    def _pick_signal(cls, signal_list: list[str], seed_str: str) -> str:
        idx = abs(hash(seed_str)) % len(signal_list)
        return signal_list[idx]
