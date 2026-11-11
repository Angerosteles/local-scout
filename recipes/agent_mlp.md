---
id: agent_mlp
family: own_network
task: classification
when: agent-designed narrow model justified by evaluation
avoid: unreviewed generated code
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: plan
license: project dependent
sources:
- https://pytorch.org/docs/stable/nn.html
requires:
  modality: {op: "=", value: text}
  labels_present: {op: "=", value: true}
  label_count: {op: ">=", value: 2}
  min_examples_per_label_train: {op: ">=", value: 5}
---

Agent supplies MLP design and test plan; human reviews. Never evaluate generated Python in this plugin. PLAN_ONLY.
