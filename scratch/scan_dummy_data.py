import os
import re

template_dir = 'templates'
findings = []

years_pattern = re.compile(r'\b(2023|2024|2025)\b')
dummy_pattern = re.compile(r'(dummy|mock|lorem|example\.com|placeholder)', re.I)

for root, _, files in os.walk(template_dir):
    for f in files:
        if f.endswith('.html'):
            fpath = os.path.join(root, f)
            with open(fpath, 'r', encoding='utf-8') as fp:
                lines = fp.readlines()
                for i, line in enumerate(lines):
                    # Skip copyright year in footer if standard
                    if '©' in line or '&copy;' in line or 'All rights reserved' in line:
                        continue
                    m_year = years_pattern.search(line)
                    if m_year:
                        findings.append((fpath, i+1, f"Hardcoded Year {m_year.group(1)}", line.strip()))
                    m_dummy = dummy_pattern.search(line)
                    if m_dummy and not 'placeholder=' in line: # ignore html placeholder attribute
                        findings.append((fpath, i+1, f"Dummy pattern {m_dummy.group(1)}", line.strip()))

print(f"Total findings: {len(findings)}")
for f in findings:
    print(f"{f[0]}:{f[1]} [{f[2]}] -> {f[3]}")
