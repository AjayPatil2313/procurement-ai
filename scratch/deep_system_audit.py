import os
import re
import sys
import inspect
import django

sys.path.insert(0, os.path.abspath('.'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.urls import get_resolver
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.db import models

print("=== 1. AUDITING ALL REGISTERED URLS & VIEWS ===")
resolver = get_resolver()

view_count = 0
unprotected_web_views = []
rf = RequestFactory()

def extract_patterns(patterns, prefix=""):
    extracted = []
    for p in patterns:
        if hasattr(p, 'url_patterns'):
            extracted.extend(extract_patterns(p.url_patterns, prefix + str(p.pattern)))
        else:
            extracted.append((prefix + str(p.pattern), p.callback, p.name))
    return extracted

all_urls = extract_patterns(resolver.url_patterns)
print(f"Total resolved URL endpoints: {len(all_urls)}")

for url, callback, name in all_urls:
    if not callback:
        continue
    mod = getattr(callback, '__module__', '')
    doc = getattr(callback, '__name__', '')
    # Check if web view under apps
    if mod.startswith('apps.') and ('web_views' in mod or 'views' in mod) and not mod.startswith('apps.ai_search.views') and not 'api' in mod:
        view_count += 1
        # Test anonymous request behavior
        req = rf.get('/' + url.lstrip('^/'))
        req.user = AnonymousUser()
        req.session = {}
        try:
            # We don't execute full POST or complex state, just inspect decorators
            # Check if login_required was wrapped:
            # login_required adds a redirect or wraps the function
            pass
        except Exception as e:
            pass

print(f"Total app views: {view_count}")

print("\n=== 2. CHECKING MISSING DATABASE INDEXES / FOREIGN KEYS ===")
from django.apps import apps
all_models = apps.get_models()
fk_without_index = []
cascade_deletions = []

for model in all_models:
    if model._meta.app_label.startswith('django') or model._meta.app_label.startswith('auth'):
        continue
    for field in model._meta.get_fields():
        if isinstance(field, models.ForeignKey):
            # Check on_delete behavior
            if field.remote_field.on_delete == models.CASCADE:
                cascade_deletions.append((model.__name__, field.name, field.remote_field.model.__name__))

print(f"CASCADE ForeignKeys count: {len(cascade_deletions)}")
for c in cascade_deletions:
    if 'User' in c[2] or 'Company' in c[2]:
        print(f"  Warning/Audit Note: {c[0]}.{c[1]} CASCADE on {c[2]}")

print("\n=== 3. AUDITING BILLING & CREDIT CHECKS ===")
for root, _, files in os.walk('apps'):
    for f in files:
        if f.endswith('.py'):
            fpath = os.path.join(root, f)
            with open(fpath, 'r', encoding='utf-8') as fp:
                lines = fp.readlines()
                for i, line in enumerate(lines):
                    if 'credits_remaining' in line:
                        print(f"Credit usage in {fpath}:{i+1} -> {line.strip()}")

