---
id: own_cnn
family: conv1d
task: classification
when: sufficient labeled local examples
avoid: missing torch or tiny unrepresentative dataset
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: executable
license: BSD-3-Clause
sources:
- https://pytorch.org/docs/stable/nn.html#conv1d
requires:
  modality: {op: "=", value: text}
  labels_present: {op: "=", value: true}
  label_count: {op: ">=", value: 2}
  min_examples_per_label_train: {op: ">=", value: 5}
  torch_available: {op: "=", value: true}
---

Optional extra torch. Fixed reviewed Conv1d template over original token sequence; no arbitrary generated code. Validation abstention, NPZ numeric weights; domain benchmark required.

Reviewed template: token IDs in original word order, Embedding(24), Conv1d(32, kernel=3), ReLU, max pool, Linear; max 64 tokens, padding/OOV=0; 40 epochs CPU. No generated code execution.
