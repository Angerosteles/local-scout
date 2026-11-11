---
id: basal_zero_shot
family: basal
task: classification
when: few or no labelled examples, other writing styles or typos; a local engine is already running
avoid: no local basal engine; the in-turn local answer path (seconds per ticket, so it forwards)
data: Masked ticket text and the label list from your dataset; optional one-line category descriptions.
cpu_ram: 'Measured offline on a 15 W laptop CPU (Ryzen 7 PRO 5850U): pico 0.8B Q8 about 2.0 s per ticket
  with 16 threads, 3.5 s on 4 cores, 1.8 GB RAM; mini 1.5B Q5 about 4.4 s, 2.2 GB.'
quality_check: No fitting. The threshold is chosen on the validation split with the same gate as the other
  methods (lowest threshold with >=5 accepted and <=10% accepted error, or --max-error); otherwise refuse.
privacy: Only masked text is sent, only to a loopback address (proxies bypassed); the engine address is not
  stored in the model artifact; "other" or low confidence means no answer.
implementation: executable
license: Apache-2.0
sources:
- https://github.com/rkinas/basal
- https://huggingface.co/Remek/basal-omni-pico
- https://huggingface.co/Remek/basal-2.0-mini
requires:
  modality: {op: "=", value: text}
  basal_available: {op: "=", value: true}
---

Zero-shot triage through basal by Remigiusz Kinas, a local typed-decision model that returns a probability for every allowed answer. Local Scout does not download, start or configure the engine. Run it yourself on the same machine, for example `basal-serve --mode gguf --host 127.0.0.1 --port 8765 ...`, then register it with `configure --basal-url http://127.0.0.1:8765` and run `setup-triage --recipe basal_zero_shot`. Each label becomes one option (its description, or the label with underscores as spaces), plus "other". The best label is accepted only when "other" does not win and its probability reaches the validation threshold. Offline measurements against Local Scout's own classifier are in the README section "Triage on real data, basal and the cascade". Thanks, Remek.
