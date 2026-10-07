from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.contrib import messages
from django.utils.text import slugify

from apps.companies.rbac import get_user_rbac_context
from apps.ai_search.models import MatchingParameter, ensure_default_parameters_for_company


@login_required
def matching_parameters_view(request):
    """
    Dynamic Matching Parameters Management Dashboard:
    Displays all configured matching parameters for the active company,
    total weight allocation, active status toggles, and allows creating,
    editing, and deleting dynamic criteria.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company module permission required.")
        return redirect("dashboard")

    # Ensure default parameters exist if company has none
    parameters = list(MatchingParameter.objects.filter(company=company).order_by("-is_active", "-weight_percentage", "id"))
    if not parameters and company:
        parameters = ensure_default_parameters_for_company(company)

    total_count = len(parameters)
    active_count = sum(1 for p in parameters if p.is_active)
    total_weight = sum(p.weight_percentage for p in parameters if p.is_active)
    mandatory_count = sum(1 for p in parameters if p.is_active and p.rule_type == MatchingParameter.RuleType.MANDATORY)

    return render(request, "ai_search/matching_parameters.html", {
        "parameters": parameters,
        "total_count": total_count,
        "active_count": active_count,
        "total_weight": total_weight,
        "mandatory_count": mandatory_count,
        "rule_choices": MatchingParameter.RuleType.choices,
        "company": company,
        "rbac": rbac,
        "page_title": "AI Company Matching Parameters",
    })


@login_required
def matching_parameter_create_view(request):
    """
    Creates a new dynamic matching score parameter for the active company.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company module permission required.")
        return redirect("dashboard")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        criteria_value = request.POST.get("criteria_value", "").strip()
        rule_type = request.POST.get("rule_type", MatchingParameter.RuleType.WEIGHTED).strip()
        weight_str = request.POST.get("weight_percentage", "10").strip()
        description = request.POST.get("description", "").strip()
        is_active = request.POST.get("is_active") == "1" or "is_active" in request.POST

        weight_val = 10
        if weight_str.isdigit():
            weight_val = max(1, min(100, int(weight_str)))

        if not name:
            messages.error(request, "Parameter name is required.")
            return redirect("matching-parameters")

        param_key = slugify(name)[:80] or "custom_param"

        MatchingParameter.objects.create(
            company=company,
            name=name[:150],
            parameter_key=param_key,
            criteria_value=criteria_value[:255],
            rule_type=rule_type,
            weight_percentage=weight_val,
            description=description,
            is_active=is_active,
        )

        messages.success(request, f"Matching parameter '{name}' was created successfully and will be applied during buyer searches.")

    return redirect("matching-parameters")


@login_required
def matching_parameter_edit_view(request, pk):
    """
    Edits an existing dynamic matching score parameter with company isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company module permission required.")
        return redirect("dashboard")

    param = get_object_or_404(MatchingParameter, pk=pk, company=company)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        criteria_value = request.POST.get("criteria_value", "").strip()
        rule_type = request.POST.get("rule_type", MatchingParameter.RuleType.WEIGHTED).strip()
        weight_str = request.POST.get("weight_percentage", "10").strip()
        description = request.POST.get("description", "").strip()
        is_active = request.POST.get("is_active") == "1" or "is_active" in request.POST

        if name:
            param.name = name[:150]
        if criteria_value:
            param.criteria_value = criteria_value[:255]
        if rule_type in [c[0] for c in MatchingParameter.RuleType.choices]:
            param.rule_type = rule_type
        if weight_str.isdigit():
            param.weight_percentage = max(1, min(100, int(weight_str)))
        param.description = description
        param.is_active = is_active
        param.save()

        messages.success(request, f"Parameter '{param.name}' updated successfully.")

    return redirect("matching-parameters")


@login_required
def matching_parameter_delete_view(request, pk):
    """
    Deletes a dynamic matching score parameter with company isolation.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company module permission required.")
        return redirect("dashboard")

    param = get_object_or_404(MatchingParameter, pk=pk, company=company)
    param_name = param.name
    param.delete()

    messages.success(request, f"Matching parameter '{param_name}' was removed.")
    return redirect("matching-parameters")


@login_required
def matching_parameter_toggle_view(request, pk):
    """
    Toggles the active state of a matching score parameter.
    Supports instant AJAX calls or standard form submission.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Access restricted"}, status=403)
        messages.error(request, "Access restricted.")
        return redirect("dashboard")

    param = get_object_or_404(MatchingParameter, pk=pk, company=company)
    param.is_active = not param.is_active
    param.save(update_fields=["is_active", "updated_at"])

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "id": param.id,
            "is_active": param.is_active,
            "name": param.name,
            "status_display": "Active" if param.is_active else "Inactive",
        })

    messages.success(request, f"Parameter '{param.name}' set to {'Active' if param.is_active else 'Inactive'}.")
    return redirect("matching-parameters")


@login_required
def matching_parameter_reset_defaults_view(request):
    """
    Restores the company's matching parameters to the 10 standard recommended B2B criteria.
    """
    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)
    company = rbac["company"]

    if not (rbac["can_view_seller"] or rbac["can_view_buyer"] or rbac["is_super_admin"]):
        messages.error(request, "Access restricted: Company module permission required.")
        return redirect("dashboard")

    if request.method == "POST":
        MatchingParameter.objects.filter(company=company).delete()
        ensure_default_parameters_for_company(company)
        messages.success(request, "Matching parameters restored to standard recommended B2B criteria (10 parameters, 100% total weight).")

    return redirect("matching-parameters")
