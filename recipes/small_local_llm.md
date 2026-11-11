---
id: small_local_llm
family: local_llm
task: general local classification
when: reviewed GGUF and local resources
avoid: unvalidated output and tight budget
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: model dependent
sources:
- https://pytorch.org/docs/stable/nn.html
requires:
  modality: {op: "=", value: text}
---

Plan only: select local LFM3 or basal by task; measure CPU, calibration and abstention first.
