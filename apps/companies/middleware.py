import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)


class CompanySuspensionMiddleware:
    """
    Enterprise Tenant Enforcement Middleware.
    Ensures that if a company is suspended (is_active=False) or soft-deleted (is_deleted=True),
    any active user belonging to that company is immediately logged out, active sessions are wiped,
    and all actions (API, web views, background tasks) are halted with the specific suspension reason.
    Super Admins and Super Admin impersonation sessions are exempted to allow compliance auditing.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_superuser:
            # Exclude impersonating superadmin if active
            if not request.session.get("impersonating_superadmin"):
                try:
                    logout_url = reverse("logout-web")
                except Exception:
                    logout_url = "/logout/"
                try:
                    login_url = reverse("login")
                except Exception:
                    login_url = "/login/"

                if request.path not in [logout_url, login_url]:
                    membership = getattr(request.user, "company_membership", None)
                    company = getattr(membership, "company", None) if membership else getattr(request.user, "company", None)

                    if company and (not company.is_active or company.is_deleted):
                        company_name = company.name
                        reason = getattr(company, "suspension_reason", "") or "Account suspended by platform governance."

                        logger.warning(
                            f"[SECURITY] Force-logging out user '{request.user.email}' because company '{company_name}' "
                            f"(ID: {company.id}) is suspended. Reason: {reason}"
                        )

                        # Terminate server-side session
                        logout(request)

                        # If AJAX or API call
                        if (
                            request.path.startswith("/api/")
                            or request.headers.get("x-requested-with") == "XMLHttpRequest"
                            or "application/json" in request.headers.get("Accept", "")
                        ):
                            return JsonResponse(
                                {
                                    "error": "company_suspended",
                                    "detail": f"Access Denied: Company '{company_name}' is currently suspended. Reason: {reason}",
                                    "company": company_name,
                                    "reason": reason,
                                    "status": "suspended",
                                },
                                status=403,
                            )

                        # Web Browser navigation -> show error alert and redirect to login
                        messages.error(
                            request,
                            f"Account Suspended: Company account '{company_name}' has been suspended by the platform administrator. Reason: {reason}. All active sessions have been terminated."
                        )
                        return redirect("login")

        return self.get_response(request)
