---
id: cloud
family: cloud
task: general reasoning
when: local quality not sufficient and transfer approved
avoid: private data without separate transfer consent
data: Approved synthetic IDs and group-separated examples only.
cpu_ram: CPU, one thread; core small corpus <3 GiB; optional weights require separate
  measurement.
quality_check: Train groups 0–7, validation 8–9, frozen test 10–11. Select minimum
  valid threshold with >=5 accepted and <=10% accepted error; otherwise refuse.
privacy: No raw text to agent or logs; no network; unknown causes no automatic fallback.
implementation: external
license: provider dependent
sources:
- https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins/
requires:
  modality: {op: "=", value: text}
  cloud_transfer_consent: {op: "=", value: true}
---

CLOUD_ONLY decision recorded; plugin has no network and performs no transfer. A human must separately decide fallback.
