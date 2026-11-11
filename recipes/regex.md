---
id: regex
family: rules
task: fields and PII
when: stable explicit patterns
avoid: irregular natural language
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: executable
license: MIT
sources: []
requires:
  modality: {op: "=", value: text}
  rule_match_ratio: {op: ">=", value: 0.9}
  rule_label_agreement: {op: ">=", value: 0.9}
---

Checksum PESEL, email, demo phone/address. Return field names only; mask in local memory.
