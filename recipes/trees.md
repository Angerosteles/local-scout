---
id: trees
family: trees
task: classification
when: nonlinear features with labels
avoid: weak sparse features or overfitting
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: executable
license: BSD-3-Clause
sources:
- https://scikit-learn.org/stable/modules/ensemble.html#forests-of-randomized-trees
requires:
  modality: {op: "=", value: text}
  labels_present: {op: "=", value: true}
  label_count: {op: ">=", value: 2}
  min_examples_per_label_train: {op: ">=", value: 5}
---

24-tree CPU forest on TF-IDF. Importance reports feature indices and weights, never terms.
