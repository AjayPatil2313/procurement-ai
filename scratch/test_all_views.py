import os
import sys
import django

sys.path.insert(0, os.path.abspath('.'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import Client
from apps.accounts.models import User
from apps.companies.models import Company, CompanyMember
from apps.catalog.models import Product, Category
from apps.requirements.models import Requirement
from apps.billing.models import Subscription

client = Client(SERVER_NAME="127.0.0.1")

# Find an admin user or superadmin
user = User.objects.filter(is_superuser=True).first()
if not user:
    user = User.objects.first()

print(f"Testing with User: {user.email if user else 'None'}")
client.force_login(user)

# Active company
membership = CompanyMember.objects.filter(user=user).first()
company = membership.company if membership else Company.objects.first()
print(f"Active Company: {company.name if company else 'None'}")

# Set session
session = client.session
if company:
    session["active_company_id"] = company.id
    session.save()

# List of web URLs to test GET
from django.urls import get_resolver

resolver = get_resolver()

def get_all_routes(patterns, prefix=""):
    routes = []
    for p in patterns:
        if hasattr(p, 'url_patterns'):
            routes.extend(get_all_routes(p.url_patterns, prefix + str(p.pattern)))
        else:
            pattern_str = prefix + str(p.pattern)
            name = getattr(p, 'name', None)
            routes.append((pattern_str, name))
    return routes

all_routes = get_all_routes(resolver.url_patterns)

get_errors = []
get_success = 0

for pattern_str, name in all_routes:
    if not name:
        continue
    # Skip api routes, static, media, admin/login/logout/register etc if parametrized
    if '<' in pattern_str:
        continue # Parametrized, skip direct test
    if pattern_str.startswith('api/'):
        continue
    if 'logout' in name:
        continue

    url = '/' + pattern_str.lstrip('^/')
    try:
        resp = client.get(url, follow=True)
        if resp.status_code >= 400:
            get_errors.append((url, name, resp.status_code, "HTTP Error"))
        else:
            get_success += 1
    except Exception as e:
        get_errors.append((url, name, 500, str(e)))

print(f"\nTotal Non-Parametrized Web Routes Checked: {get_success + len(get_errors)}")
print(f"Successful (200-302): {get_success}")
print(f"Errors: {len(get_errors)}")
for err in get_errors:
    print("  -> ERROR:", err)
