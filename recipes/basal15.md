---
id: basal15
family: basal
task: legacy ID compatibility
when: old callers need a migration hint
avoid: training or use of obsolete model
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: Apache-2.0
sources:
- https://huggingface.co/Remek/basal-omni-pico
requires:
  modality: {op: "=", value: text}
---

Legacy recipe ID kept as a plan-only card so older callers get a clear answer. Use basal_zero_shot (basal-omni-pico or basal-2.0-mini through a local basal engine); no obsolete weights are used.
