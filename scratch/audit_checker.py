import os
import re
import django

import sys
sys.path.insert(0, os.path.abspath('.'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.urls import reverse, NoReverseMatch, get_resolver

template_dir = 'templates'
pattern = re.compile(r'{%\s*url\s+[\'"]([a-zA-Z0-9_\-:]+)[\'"]')

broken = []
total_found = 0

for root, _, files in os.walk(template_dir):
    for f in files:
        if f.endswith('.html'):
            filepath = os.path.join(root, f)
            with open(filepath, 'r', encoding='utf-8') as fp:
                content = fp.read()
                matches = pattern.findall(content)
                for m in set(matches):
                    total_found += 1
                    # Test if url name exists in resolver
                    try:
                        reverse(m)
                    except NoReverseMatch as e:
                        # Try dummy args
                        try:
                            reverse(m, args=[1])
                        except NoReverseMatch:
                            try:
                                reverse(m, kwargs={'pk': 1})
                            except NoReverseMatch:
                                try:
                                    reverse(m, kwargs={'company_id': 1})
                                except NoReverseMatch:
                                    try:
                                        reverse(m, kwargs={'item_id': 1})
                                    except NoReverseMatch:
                                        try:
                                            reverse(m, kwargs={'lead_id': 1})
                                        except NoReverseMatch:
                                            try:
                                                reverse(m, kwargs={'result_id': 1})
                                            except NoReverseMatch:
                                                broken.append((filepath, m, str(e)))

print(f"Total unique URL references per template: {total_found}")
print(f"Broken URL references: {len(broken)}")
for filepath, m, err in broken:
    print(f"File: {filepath} | Route Name: {m} | Error: {err}")
