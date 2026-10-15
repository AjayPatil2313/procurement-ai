"""
TIER 1: COGNITIVE AI GENERATION & CLOUD FAILOVER ENGINE
Unified gateway orchestrating primary AI reasoning (Google Gemini 2.5 Flash)
with an automated Cloud Failover Engine (OpenAI gpt-4o-mini Backup)
triggered automatically upon HTTP 429, HTTP 503, Request Timeout, or Network Failure.
Provides real-time callback hooks and execution telemetry.
"""

import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Registered global failover callbacks
_GLOBAL_FAILOVER_CALLBACKS: List[Callable[[Dict[str, Any]], None]] = []


def register_global_failover_callback(fn: Callable[[Dict[str, Any]], None]) -> None:
    """Registers a listener called whenever a cloud failover occurs."""
    if fn not in _GLOBAL_FAILOVER_CALLBACKS:
        _GLOBAL_FAILOVER_CALLBACKS.append(fn)


class AIFailoverGateway:
    """
    Tier 1 AI Gateway implementing:
    1. Primary Engine: Google Gemini 2.5 Flash (top-loaded system instructions & JSON output).
    2. Cloud Failover Engine: OpenAI gpt-4o-mini Backup triggered on HTTP 429, 503, or Timeout.
    3. Telemetry & Callback System: logs latency, status codes, and failover reasons.
    """

    PRIMARY_ENGINE_NAME = "Google Gemini 2.5 Flash"
    PRIMARY_DEFAULT_MODEL = "gemini-2.5-flash"
    FAILOVER_ENGINE_NAME = "OpenAI gpt-4o-mini Backup"
    FAILOVER_DEFAULT_MODEL = "gpt-4o-mini"

    @classmethod
    def get_gemini_key(cls) -> str:
        return (
            os.getenv("GEMINI_API_KEY", "").strip()
            or getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )

    @classmethod
    def get_openai_key(cls) -> str:
        return (
            os.getenv("OPENAI_API_KEY", "").strip()
            or getattr(settings, "OPENAI_API_KEY", "")
        )

    @classmethod
    def generate(
        cls,
        prompt: str,
        system_instruction: str = "You are an expert B2B Procurement and Sales Intelligence Engine. Output only valid JSON.",
        temperature: float = 0.2,
        max_tokens: int = 700,
        timeout: float = 4.5,
        simulate_error: Optional[str] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        search_job: Optional[Any] = None,
    ) -> Tuple[Optional[Union[Dict[str, Any], List[Any]]], Dict[str, Any]]:
        """
        Executes AI generation according to Tier 1 Cloud Failover Architecture:
        - Tries Google Gemini 2.5 Flash first.
        - If HTTP 200 OK -> returns candidate stream.
        - If HTTP 429 / 503 / Timeout / Error -> triggers Cloud Failover to OpenAI gpt-4o-mini Backup.
        - If failover succeeds (HTTP 200 OK) -> returns candidate stream with failover metadata.
        - Fires registered failover callbacks and logs telemetry.
        """
        start_time = time.time()
        gemini_key = cls.get_gemini_key()
        openai_key = cls.get_openai_key()

        telemetry: Dict[str, Any] = {
            "success": False,
            "engine_used": None,
            "provider_code": None,
            "failover_occurred": False,
            "failover_trigger": None,
            "primary_status_code": None,
            "failover_status_code": None,
            "primary_latency_ms": 0,
            "failover_latency_ms": 0,
            "total_latency_ms": 0,
            "error": None,
        }

        # -------------------------------------------------------------
        # STEP 1: Execute Primary Engine (Google Gemini 2.5 Flash)
        # -------------------------------------------------------------
        gemini_start = time.time()
        gemini_success = False
        gemini_result = None
        gemini_error = None
        gemini_status_code = None

        if simulate_error:
            # Simulation hook for live console testing
            gemini_error = f"Simulated {simulate_error}"
            if "429" in simulate_error:
                gemini_status_code = 429
            elif "503" in simulate_error:
                gemini_status_code = 503
            elif "timeout" in simulate_error.lower():
                gemini_status_code = 408
            else:
                gemini_status_code = 500
        elif not gemini_key:
            gemini_error = "GEMINI_API_KEY missing from environment"
            gemini_status_code = 401
        else:
            try:
                gemini_res, gemini_status_code = cls._call_gemini(
                    api_key=gemini_key,
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                if gemini_status_code == 200 and gemini_res is not None:
                    gemini_result = gemini_res
                    gemini_success = True
                else:
                    gemini_error = f"HTTP {gemini_status_code}"
            except requests.exceptions.Timeout:
                gemini_status_code = 408
                gemini_error = "Request Timeout"
            except requests.exceptions.RequestException as req_err:
                gemini_status_code = getattr(getattr(req_err, "response", None), "status_code", 500)
                gemini_error = f"Connection / Network Error: {req_err}"
            except Exception as e:
                gemini_status_code = 500
                gemini_error = str(e)

        telemetry["primary_status_code"] = gemini_status_code
        telemetry["primary_latency_ms"] = int((time.time() - gemini_start) * 1000)

        # Happy Path: Gemini 200 OK
        if gemini_success and gemini_result is not None:
            telemetry["success"] = True
            telemetry["engine_used"] = cls.PRIMARY_ENGINE_NAME
            telemetry["provider_code"] = "GEMINI"
            telemetry["total_latency_ms"] = telemetry["primary_latency_ms"]

            cls._record_api_log(
                provider=f"{cls.PRIMARY_ENGINE_NAME} (Primary)",
                endpoint="generateContent",
                status_code=200,
                search_job=search_job,
            )
            return gemini_result, telemetry

        # -------------------------------------------------------------
        # STEP 2: Trigger Cloud Failover Engine (OpenAI gpt-4o-mini Backup)
        # -------------------------------------------------------------
        failover_reason = f"Primary {cls.PRIMARY_ENGINE_NAME} failed [{gemini_error or 'Unknown Error'} / HTTP {gemini_status_code}]"
        logger.warning(f"[AI FAILOVER TRIPPED] {failover_reason}. Activating {cls.FAILOVER_ENGINE_NAME}...")

        telemetry["failover_occurred"] = True
        telemetry["failover_trigger"] = failover_reason

        openai_start = time.time()
        openai_success = False
        openai_result = None
        openai_status_code = None

        if not openai_key:
            telemetry["error"] = f"{failover_reason} -> OPENAI_API_KEY missing for cloud failover"
            logger.error("[AI FAILOVER FAILED] OPENAI_API_KEY is not configured.")
        else:
            try:
                openai_res, openai_status_code = cls._call_openai(
                    api_key=openai_key,
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout + 1.0,  # slightly more forgiving timeout on failover
                )
                if openai_status_code == 200 and openai_res is not None:
                    openai_result = openai_res
                    openai_success = True
                else:
                    telemetry["error"] = f"OpenAI failover returned HTTP {openai_status_code}"
            except requests.exceptions.Timeout:
                openai_status_code = 408
                telemetry["error"] = "OpenAI failover timed out"
            except Exception as e:
                openai_status_code = 500
                telemetry["error"] = f"OpenAI failover exception: {e}"

        telemetry["failover_status_code"] = openai_status_code
        telemetry["failover_latency_ms"] = int((time.time() - openai_start) * 1000)
        telemetry["total_latency_ms"] = int((time.time() - start_time) * 1000)

        # Failover Event Payload for Callbacks
        failover_event = {
            "timestamp": timezone.now().isoformat(),
            "primary_engine": cls.PRIMARY_ENGINE_NAME,
            "primary_status_code": gemini_status_code,
            "primary_error": gemini_error,
            "failover_engine": cls.FAILOVER_ENGINE_NAME,
            "failover_status_code": openai_status_code,
            "failover_success": openai_success,
            "latency_ms": telemetry["total_latency_ms"],
            "prompt_snippet": prompt[:120] + "...",
            "search_job_id": getattr(search_job, "id", None),
        }

        # Invoke instance and global failover callbacks
        cls._trigger_callbacks(failover_event, custom_callback=callback)

        if openai_success and openai_result is not None:
            telemetry["success"] = True
            telemetry["engine_used"] = cls.FAILOVER_ENGINE_NAME
            telemetry["provider_code"] = "OPENAI"

            cls._record_api_log(
                provider=f"{cls.FAILOVER_ENGINE_NAME} (Cloud Failover from Gemini)",
                endpoint="chat/completions",
                status_code=200,
                search_job=search_job,
            )
            return openai_result, telemetry

        # Both failed
        telemetry["success"] = False
        cls._record_api_log(
            provider=f"AI Gateway (Gemini {gemini_status_code} & OpenAI {openai_status_code} Failed)",
            endpoint="gateway_failover_exhausted",
            status_code=gemini_status_code or 500,
            search_job=search_job,
        )
        return None, telemetry

    # -----------------------------------------------------------------
    # Primary Implementation: Google Gemini
    # -----------------------------------------------------------------
    @classmethod
    def _call_gemini(
        cls,
        api_key: str,
        prompt: str,
        system_instruction: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> Tuple[Optional[Union[Dict[str, Any], List[Any]]], int]:
        """Calls Google Gemini REST API (gemini-2.5-flash / gemini-1.5-flash fallback)."""
        models_to_try = [
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "gemini-1.5-flash",
            "gemini-3.5-flash-lite",
        ]

        last_status = 500
        for model in models_to_try:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "systemInstruction": {
                    "parts": [{"text": system_instruction}]
                },
                "contents": [
                    {"parts": [{"text": prompt}]}
                ],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                },
            }

            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                last_status = resp.status_code
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        parsed = cls._parse_json_safely(text)
                        if parsed is not None:
                            return parsed, 200
                elif resp.status_code in (429, 503):
                    # Rate limit or overload -> immediate failover trigger
                    return None, resp.status_code
            except requests.exceptions.Timeout:
                return None, 408
            except Exception:
                continue

        return None, last_status

    # -----------------------------------------------------------------
    # Failover Implementation: OpenAI gpt-4o-mini
    # -----------------------------------------------------------------
    @classmethod
    def _call_openai(
        cls,
        api_key: str,
        prompt: str,
        system_instruction: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> Tuple[Optional[Union[Dict[str, Any], List[Any]]], int]:
        """Calls OpenAI Chat Completions API with gpt-4o-mini."""
        endpoint = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": os.getenv("OPENAI_MODEL", cls.FAILOVER_DEFAULT_MODEL),
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                parsed = cls._parse_json_safely(content)
                if parsed is not None:
                    return parsed, 200
        return None, resp.status_code

    # -----------------------------------------------------------------
    # JSON Parsing & Sanitation
    # -----------------------------------------------------------------
    @classmethod
    def _parse_json_safely(cls, raw_text: str) -> Optional[Union[Dict[str, Any], List[Any]]]:
        if not raw_text or not raw_text.strip():
            return None
        clean = raw_text.strip()
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)

        # Attempt 1: direct parse
        try:
            return json.loads(clean)
        except Exception:
            pass

        # Attempt 2: isolate first '{' or '[' to matching ending
        match_obj = re.search(r"(\{.*\}|\[.*\])", clean, re.DOTALL)
        if match_obj:
            try:
                return json.loads(match_obj.group(0))
            except Exception:
                pass

        return None

    # -----------------------------------------------------------------
    # Callbacks & Logging
    # -----------------------------------------------------------------
    @classmethod
    def _trigger_callbacks(
        cls,
        event: Dict[str, Any],
        custom_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Dispatches event to custom callback and registered global callbacks."""
        if custom_callback:
            try:
                custom_callback(event)
            except Exception as e:
                logger.error(f"Error in custom failover callback: {e}")

        for fn in _GLOBAL_FAILOVER_CALLBACKS:
            try:
                fn(event)
            except Exception as e:
                logger.error(f"Error in global failover callback: {e}")

    @classmethod
    def _record_api_log(
        cls,
        provider: str,
        endpoint: str,
        status_code: int,
        search_job: Optional[Any] = None,
    ) -> None:
        """Asynchronously or safely writes entry to APILog in database."""
        try:
            from apps.ai_search.models import APILog
            APILog.objects.create(
                provider=provider[:100],
                endpoint=endpoint[:255],
                status_code=status_code,
                search_job=search_job,
            )
        except Exception as e:
            logger.debug(f"Could not record APILog: {e}")
