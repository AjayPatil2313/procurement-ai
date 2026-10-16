from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from apps.companies.rbac import get_user_rbac_context
from apps.notifications.models import NotificationPreference


@login_required
def user_notification_preferences_view(request):
    """
    User Notification Preferences View:
    Allows authenticated users to toggle email & in-app alerts per notification category.
    Mandatory security & billing alerts are enforced and preserved.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    categories = NotificationPreference.Category.choices
    
    # Fetch or initialize user preferences
    prefs = {
        p.category: p
        for p in NotificationPreference.objects.filter(user=request.user, company=company)
    }

    if request.method == "POST":
        for cat_code, cat_label in categories:
            # SECURITY and BILLING are mandatory for enterprise account integrity
            if cat_code in (NotificationPreference.Category.SECURITY, NotificationPreference.Category.BILLING):
                email_enabled = True
                in_app_enabled = True
                push_enabled = True
            else:
                email_enabled = request.POST.get(f"email_{cat_code}") == "on"
                in_app_enabled = request.POST.get(f"in_app_{cat_code}") == "on"
                push_enabled = request.POST.get(f"push_{cat_code}") == "on"

            NotificationPreference.objects.update_or_create(
                user=request.user,
                company=company,
                category=cat_code,
                defaults={
                    "email_enabled": email_enabled,
                    "in_app_enabled": in_app_enabled,
                    "push_enabled": push_enabled,
                },
            )

        messages.success(request, "Notification preferences updated successfully.")
        return redirect("user-notification-preferences")

    # Build preference list with status
    pref_list = []
    for cat_code, cat_label in categories:
        p = prefs.get(cat_code)
        is_mandatory = cat_code in (NotificationPreference.Category.SECURITY, NotificationPreference.Category.BILLING)
        pref_list.append({
            "code": cat_code,
            "label": cat_label,
            "email_enabled": True if is_mandatory else (p.email_enabled if p else True),
            "in_app_enabled": True if is_mandatory else (p.in_app_enabled if p else True),
            "push_enabled": True if is_mandatory else (getattr(p, "push_enabled", True) if p else True),
            "is_mandatory": is_mandatory,
        })

    context = {
        "page_title": "Notification Preferences",
        "company": company,
        "rbac": rbac,
        "pref_list": pref_list,
    }
    return render(request, "notifications/preferences.html", context)


def public_unsubscribe_view(request):
    """
    Public 1-Click Unsubscribe Endpoint (RFC 8058 & CAN-SPAM compliant).
    Allows recipient to unsubscribe without login session using a cryptographically signed token.
    Supports RFC 8058 POST requests with 'List-Unsubscribe=One-Click'.
    """
    from django.http import HttpResponse, JsonResponse
    from apps.notifications.services.unsubscribe_service import (
        verify_unsubscribe_token,
        process_user_unsubscribe,
    )

    token = request.GET.get("token") or request.POST.get("token")
    user, category, is_valid, error_msg = verify_unsubscribe_token(token)

    # Check for RFC 8058 automated POST request
    is_rfc8058_post = (
        request.method == "POST"
        and (
            request.POST.get("List-Unsubscribe") == "One-Click"
            or request.headers.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"
        )
    )

    if not is_valid:
        if is_rfc8058_post:
            return HttpResponse("Invalid token", status=400)
        return render(request, "notifications/unsubscribe.html", {"error": error_msg})

    categories_dict = dict(NotificationPreference.Category.choices)
    category_label = categories_dict.get(category, category)

    if request.method == "POST":
        action = request.POST.get("action", "category")
        target_cat = "ALL" if action == "all" else category
        success, message = process_user_unsubscribe(user, target_cat)

        if is_rfc8058_post:
            return HttpResponse("Unsubscribe successful", status=200)

        return render(request, "notifications/unsubscribe.html", {
            "success": success,
            "message": message,
            "user": user,
        })

    # GET request - Show confirmation page
    return render(request, "notifications/unsubscribe.html", {
        "user": user,
        "category": category,
        "category_label": category_label,
        "token": token,
    })

