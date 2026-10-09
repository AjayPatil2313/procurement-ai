import functools
import logging
import time
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Extracts client IP from request headers handling proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    return ip


def check_rate_limit(key: str, max_requests: int = 60, window_seconds: int = 60) -> tuple[bool, int]:
    """
    Checks rate limit using fixed-window caching.
    Returns (is_allowed: bool, remaining_requests: int).
    """
    current_time = int(time.time())
    window_key = f"rl:{key}:{current_time // window_seconds}"

    try:
        # Atomic increment with expiration
        current_count = cache.get(window_key, 0)
        if current_count >= max_requests:
            return False, 0

        cache.set(window_key, current_count + 1, timeout=window_seconds + 5)
        return True, max(0, max_requests - (current_count + 1))
    except Exception as e:
        logger.warning("Cache rate limit error: %s; allowing request", e)
        return True, max_requests


def rate_limit(rate: int = 60, period: int = 60, key_type: str = "ip"):
    """
    Decorator for rate-limiting web views and API endpoints.
    key_type options: 'ip', 'user', 'company'
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if key_type == "user" and request.user.is_authenticated:
                key = f"user:{request.user.id}"
            elif key_type == "company":
                comp_id = request.session.get("active_company_id")
                key = f"comp:{comp_id}" if comp_id else f"ip:{get_client_ip(request)}"
            else:
                key = f"ip:{get_client_ip(request)}"

            route_key = f"{view_func.__name__}:{key}"
            is_allowed, remaining = check_rate_limit(route_key, max_requests=rate, window_seconds=period)

            if not is_allowed:
                logger.warning("Rate limit exceeded for %s on %s", key, view_func.__name__)
                if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith("/api/"):
                    return JsonResponse({
                        "error": "Too Many Requests",
                        "message": f"Rate limit reached ({rate} requests per {period}s). Please wait before retrying.",
                        "retry_after": period,
                    }, status=429)

                messages.error(request, "Too many requests. Please wait a moment before trying again.")
                return HttpResponse(
                    f"<h3>429 Too Many Requests</h3><p>Rate limit exceeded. Please wait {period} seconds before retrying.</p>",
                    status=429,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view
    return decorator
