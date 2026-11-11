---
id: embed_search
family: vectors
task: search
when: offline nearest-source lookup
avoid: cross-language semantics without shared terms
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: executable
license: BSD-3-Clause
sources: []
requires:
  modality: {op: "=", value: text}
---

Offline TF-IDF + truncated SVD (LSA), fitted only on train; cosine top-3 with source. This is not a pretrained semantic encoder. Cost and quality require measurement on the target corpus.
