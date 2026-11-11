---
id: lfm25_moe
family: local_llm
task: tool reasoning
when: complex local agent tasks
avoid: limited RAM
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: LFM Open License v1.0; verify commercial terms
sources:
- https://huggingface.co/LiquidAI/LFM2.5-24B-A2B
requires:
  modality: {op: "=", value: text}
---

Plan only: 24B total, about 2B active; Q4 needs at least 12 GB for weights plus runtime, above the memory this plugin is tested with. No download or execution.
