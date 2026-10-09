import os
import re

patterns_to_check = [
    r'([A-Z][a-zA-Z]+)\.objects\.get\(',
    r'([A-Z][a-zA-Z]+)\.objects\.filter\(',
]

company_scoped_models = [
    'Product', 'Requirement', 'Inquiry', 'SearchJob', 'MatchingParameter',
    'SavedItem', 'CompanyMember', 'CompanyRole', 'Subscription', 'CreditTransaction',
    'ActivityLog', 'SupportTicket'
]

findings = []

for root, _, files in os.walk('apps'):
    for f in files:
        if f.endswith('.py') and not f.startswith('test') and not 'migrations' in root:
            fpath = os.path.join(root, f)
            with open(fpath, 'r', encoding='utf-8') as fp:
                lines = fp.readlines()
                for i, line in enumerate(lines):
                    for m in company_scoped_models:
                        if f"{m}.objects.get(" in line:
                            # inspect surrounding 5 lines
                            context = "".join(lines[max(0, i-2):min(len(lines), i+4)])
                            if "company" not in context and "pk" in line or "id" in line:
                                findings.append((fpath, i+1, line.strip(), context))

print(f"Total potential unscoped .get() calls found: {len(findings)}")
for fpath, lineno, line, ctx in findings[:25]:
    print(f"\n{fpath}:{lineno} -> {line}")
    print("Context:")
    print(ctx)
