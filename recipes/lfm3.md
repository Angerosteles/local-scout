---
id: lfm3
family: local_llm
task: text and modality variants
when: general local reasoning
avoid: unverified backend or license
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: LFM Open License v1.0; verify variant
sources:
- https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF
requires:
  modality: {op: "=", value: text}
---

Plan from frozen contract: text 1.7B; Q4_K_M about 1.1 GB on disk, runtime larger. CPU latency unmeasured. Select modality variant deliberately.
