from .models import Company, CompanyMember
from .rbac import get_user_rbac_context


def rbac_context(request):
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "rbac": {
                "is_authenticated": False,
                "role": "GUEST",
                "role_label": "Guest",
                "is_super_admin": False,
                "is_company_admin": False,
                "is_company_user": False,
                "can_view_buyer": False,
                "can_view_seller": False,
                "company": None,
                "membership": None,
            },
            "current_company": None,
            "all_user_companies": [],
            "notifications_count": 0,
            "search_query": "",
        }

    selected_company_id = request.session.get("active_company_id")
    rbac = get_user_rbac_context(request.user, company_id=selected_company_id)

    if rbac["is_super_admin"]:
        all_companies = Company.objects.filter(is_active=True).order_by("name")
    else:
        memberships = CompanyMember.objects.filter(
            user=request.user,
            is_active=True,
            company__is_active=True,
        ).select_related("company")
        all_companies = [m.company for m in memberships]

    search_query = request.GET.get("q", "").strip() if hasattr(request, "GET") else ""

    return {
        "rbac": rbac,
        "current_company": rbac["company"],
        "current_membership": rbac["membership"],
        "all_user_companies": all_companies,
        "notifications_count": 3,
        "search_query": search_query,
    }
