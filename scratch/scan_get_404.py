import os
import re

patterns = [
    r'get_object_or_404\(([A-Za-z]+)',
]

findings = []
for root, _, files in os.walk('apps'):
    for f in files:
        if f.endswith('.py') and not f.startswith('test') and not 'migrations' in root:
            fpath = os.path.join(root, f)
            with open(fpath, 'r', encoding='utf-8') as fp:
                content = fp.read()
                matches = re.finditer(r'get_object_or_404\(([A-Za-z]+),\s*([^)]+)\)', content)
                for m in matches:
                    model = m.group(1)
                    args = m.group(2)
                    if model in ['Product', 'Requirement', 'Inquiry', 'SearchJob', 'MatchingParameter', 'SavedItem', 'CompanyMember', 'CompanyRole', 'Subscription']:
                        if 'company' not in args:
                            findings.append((fpath, model, args))

print(f"Total unscoped get_object_or_404 calls: {len(findings)}")
for f in findings:
    print(f)
