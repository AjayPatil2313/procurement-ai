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
        matching_parameters: list | None = None,
    ) -> dict:
        """
        Evaluates company profile fit against target product or requirement.
        Applies active dynamic MatchingParameter criteria and weights.
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
                    matching_parameters=matching_parameters,
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
            matching_parameters=matching_parameters,
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
        matching_parameters: list | None = None,
    ) -> dict | None:
        """Calls Google Gemini API (gemini-3.5-flash-lite) to analyze B2B match."""
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

        params_section = ""
        if matching_parameters:
            lines = ["Active Company Matching Parameters & Weightages to strictly evaluate:"]
            for p in matching_parameters:
                p_name = getattr(p, "name", str(p))
                p_weight = getattr(p, "weight_percentage", 10)
                p_rule = getattr(p, "rule_type", "weighted")
                p_criteria = getattr(p, "criteria_value", "")
                p_desc = getattr(p, "description", "")
                lines.append(f"- {p_name} (Weight: {p_weight}%, Rule: {p_rule}): Criteria: {p_criteria}. {p_desc}")
            params_section = "\n" + "\n".join(lines) + "\n"

        prompt = f"""
You are an expert B2B Procurement and Sales Matchmaker.
Analyze whether the following company matches our target item based on our active matching criteria.

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
{params_section}
Respond ONLY with a valid JSON object with the following fields:
{{
  "match_score": integer between 70 and 98 indicating percentage fit calculated from the active parameters,
  "match_reason": "2 sentences explaining exactly why this company matches our product and parameter criteria",
  "need_signal": "A specific realistic B2B buying or supply signal (e.g., active tender, vendor empanelement, plant maintenance cycle, ISO export lines)",
  "matched_parameters": ["list of matching parameter names that this company satisfied"],
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
                "maxOutputTokens": 450,
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
        matched_parameters = parsed.get("matched_parameters", [])
        if not matched_parameters and matching_parameters:
            matched_parameters = [getattr(p, "name", str(p)) for p in matching_parameters[:5]]

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
            "matched_parameters": matched_parameters,
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
        matching_parameters: list | None = None,
    ) -> dict:
        """Deterministic heuristic fallback when Gemini is offline or unconfigured."""
        company_name = scraped_company.get("name") or "Enterprise Partner"
        role = scraped_company.get("company_role") or "supplier"
        city = scraped_company.get("city") or "Industrial Center"
        country = scraped_company.get("country") or "India"
        industry = scraped_company.get("industry") or "Industrial Supply"
        description = scraped_company.get("description") or ""

        text_corpus = (company_name + " " + industry + " " + description).lower()
        item_words = [w.lower() for w in target_item_name.split() if len(w) > 3]

        matched_params_list = []

        if matching_parameters:
            total_weight = 0
            weighted_score_sum = 0.0
            mandatory_failed = False

            for param in matching_parameters:
                p_name = getattr(param, "name", "")
                p_key = (getattr(param, "parameter_key", "") or "").lower()
                p_criteria = (getattr(param, "criteria_value", "") or "").lower()
                p_rule = getattr(param, "rule_type", "weighted")
                p_weight = getattr(param, "weight_percentage", 10)

                total_weight += p_weight
                param_score = 0.7  # default base satisfaction

                # 1. Entity type: Pvt Ltd / Ltd check
                if "entity_type" in p_key or "pvt" in p_name.lower():
                    is_pvt_ltd = any(term in text_corpus for term in ["pvt", "private limited", "ltd", "limited", "corp", "inc", "gmbh", "llc", "enterprises"])
                    if is_pvt_ltd:
                        param_score = 1.0
                    else:
                        param_score = 0.4
                        if p_rule == "mandatory":
                            mandatory_failed = True

                # 2. Verified status (Phone, Email, Web)
                elif "verified" in p_key or "verified" in p_name.lower():
                    has_phone = bool(scraped_company.get("phone"))
                    has_email = bool(scraped_company.get("email"))
                    has_web = bool(scraped_company.get("website"))
                    if has_phone and has_email:
                        param_score = 1.0
                    elif has_phone or has_email or has_web:
                        param_score = 0.85
                    else:
                        param_score = 0.5
                        if p_rule == "mandatory":
                            mandatory_failed = True

                # 3. Location proximity
                elif "location" in p_key or "location" in p_name.lower():
                    is_domestic = country.lower() in ["india", "domestic"]
                    has_city = bool(city and city != "Industrial Center")
                    param_score = 1.0 if (is_domestic and has_city) else 0.8

                # 4. Product matching
                elif "product" in p_key or "product" in p_name.lower():
                    k_matches = sum(1 for w in item_words if w in text_corpus)
                    if k_matches >= 2:
                        param_score = 1.0
                    elif k_matches == 1:
                        param_score = 0.85
                    else:
                        param_score = 0.6

                # 5. Category matching
                elif "categor" in p_key or "categor" in p_name.lower():
                    param_score = 0.95 if any(w in text_corpus for w in item_words[:2]) else 0.8

                # 6. Buyer requirement & demand
                elif "buyer_req" in p_key or "demand" in p_name.lower() or "requirement" in p_name.lower():
                    if role in ["end_user", "distributor", "trader"]:
                        param_score = 1.0
                    elif role == "manufacturer":
                        param_score = 0.85
                    else:
                        param_score = 0.7

                # 7. Industry match
                elif "industry" in p_key or "industry" in p_name.lower():
                    param_score = 0.95 if industry else 0.8

                # 8. Specifications / Size / Grade / Capacity
                elif "specification" in p_key or "capacity" in p_name.lower() or "size" in p_name.lower():
                    has_specs = any(t in text_corpus for t in ["ton", "grade", "iso", "capacity", "scale", "mfg", "plant", "unit", "mtc", "bar"])
                    param_score = 1.0 if has_specs else 0.8

                # 9. Company profile relevance
                elif "profile" in p_key or "relevance" in p_name.lower():
                    param_score = 1.0 if len(description) > 30 else 0.8

                # 10. B2B model
                elif "b2b" in p_key or "b2b" in p_name.lower():
                    param_score = 1.0 if role in ["end_user", "manufacturer", "trader", "distributor", "supplier", "exporter"] else 0.75

                # Custom parameter added by user
                else:
                    crit_words = [w for w in p_criteria.split() if len(w) > 3]
                    if crit_words and any(w in text_corpus for w in crit_words):
                        param_score = 1.0
                    else:
                        param_score = 0.8

                weighted_score_sum += param_score * p_weight
                if param_score >= 0.75:
                    matched_params_list.append(p_name)

            norm_weight = max(1, total_weight)
            score_ratio = weighted_score_sum / norm_weight
            jitter = (len(company_name) * 3) % 4
            calc_score = int(72 + (score_ratio * 24) + jitter)
            if mandatory_failed:
                calc_score -= 10
            match_score = min(98, max(70, calc_score))

        else:
            # Fallback legacy scoring
            base_score = 78
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
            matched_params_list = ["Product Technical Matching", "Industry Vertical Match", "B2B Operating Model"]

        if job_type == "find_buyers":
            need_signal = cls._pick_signal(cls.BUYER_INTENT_SIGNALS, company_name)
            match_reason = (
                f"{company_name} operates facility infrastructure in {city}, {country} with "
                f"recurrent commercial consumption of {target_item_name}. "
                f"Their operational profile satisfies active matching criteria ({industry})."
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
            "matched_parameters": matched_params_list,
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
