---
id: minicpm_v47
family: multimodal
task: image and text
when: flexible visual analysis
avoid: small RAM budget
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: Apache-2.0
sources:
- https://huggingface.co/openbmb/MiniCPM-V-4.7-1B
requires:
  modality: {op: "=", value: text}
---

Plan from frozen contract: 1B variant plus encoder can approach 2B total; Q4 weights roughly 0.5–1 GB, runtime extra. 35B-A3B roughly 17.5 GB Q4 weights. CPU performance unknown; never download.
