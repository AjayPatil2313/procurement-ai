from django import template

register = template.Library()


@register.simple_tag
def has_permission(rbac, module, action="READ"):
    if not rbac or not rbac.get("is_authenticated"):
        return False
    if rbac.get("is_super_admin") or rbac.get("is_company_admin"):
        return True
    perms = rbac.get("permissions", set())
    return f"{module}:{action}" in perms or f"{module}:*" in perms


@register.filter
def can_access(rbac, module):
    if not rbac or not rbac.get("is_authenticated"):
        return False
    if rbac.get("is_super_admin") or rbac.get("is_company_admin"):
        return True
    perms = rbac.get("permissions", set())
    return any(p.startswith(f"{module}:") for p in perms)
