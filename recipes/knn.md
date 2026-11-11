---
id: knn
family: neighbors
task: classification
when: small labeled corpus with cosine structure
avoid: large corpus or ambiguous neighbors
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: executable
license: BSD-3-Clause
sources:
- https://scikit-learn.org/stable/modules/neighbors.html
requires:
  modality: {op: "=", value: text}
  labels_present: {op: "=", value: true}
  label_count: {op: ">=", value: 2}
  min_examples_per_label_train: {op: ">=", value: 5}
---

Similarity-weighted voting among up to five cosine TF-IDF neighbors; validation selects maximum nearest distance 1-threshold; zero vector abstains.
