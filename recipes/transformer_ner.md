---
id: transformer_ner
family: transformer
task: NER
when: enough labeled spans and reviewed small model
avoid: no labels or memory budget
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

Plan only: local small transformer/NER, grouped validation and span metrics before activation.
