# Evidence: measurements and how they were taken

Part of the [Local Scout README](../README.md).

## What Hermes already does, and what Local Scout adds

The same synthetic content returned by `read_file`, `terminal` and a local MCP stdio tool in a real Hermes 0.23 process (no model call), recorded after Hermes' own redaction and the plugin hooks. One sample per cell, so this shows coverage, not detection rates. Hermes partly redacts phone numbers written as `+12125550147` without spaces; that form was not in the sample.

| Category | Hermes alone: read_file / terminal / MCP | With Local Scout (`mask`) |
|---|---|---|
| E-mail, phones (`212-555-0147`, `+1 212-555-0147`), card, SSN, IBAN, US address | in clear on all three | masked on all three |
| Person name | in clear | in clear: kept in tool output on purpose |
| Stripe, GitHub and AWS keys | replaced by a sentinel (`«redacted:sk_live_…»`) / first and last characters kept / **in clear** | Hermes' sentinel stays (file) / masked / masked |
| Bearer token | in clear on all three | masked on all three |

## Triage on real data, basal and the cascade (measured offline)

Triage needs calibration on your own data. These measurements show what to expect. For every method the threshold was chosen once on clean validation data for at most 5% error among local answers, then applied to held-out test data. Cells show **handled locally / error among local answers**. Small sets move a few points, and in several rows the observed test error is above the 5% target.

| Data | Local Scout (trained) | basal-omni-pico 0.8B (zero-shot) | basal-2.0-mini 1.5B (zero-shot) |
|---|---|---|---|
| Synthetic demo tickets (83 validation rows, no test split; template-generated, easy) | 100% / 4.8% | 93% / 0% | 95% / 0% |
| CLINC150: 15 banking intents + out-of-scope queries (450 test tickets) | 45.6% / 5.9%; top-1 94% | 53.8% / 5.8%; top-1 89% | 57.3% / 4.7%; top-1 89% |
| CLINC subset (235 tickets), clean | 42.6% / 7.0% | 50.6% / 7.6% | 55.7% / 6.9% |
| Same subset, light typos (15% of words) | 39.1% / 9.8% | 50.6% / 10.1% | 53.2% / 8.8% |
| Same subset, heavy typos (35% of words) | 32.3% / 13.2%; top-1 80% | 38.7% / 7.7%; top-1 84% | 46.4% / 5.5%; top-1 81% |
| BANKING77: other authors, 6 shared topics + 10 out of scope (150 tickets) | 21.3% / 3.1%; top-1 63% | 44.0% / 1.5%; top-1 80% | 41.3% / 3.2%; top-1 80% |
| Labelled examples needed | 25 per category for the CLINC row (5 → 25%, 10 → 36% handled; mean of 3 draws) | none: one-line category descriptions | none |

Local Scout is very fast and precise on the wording it was trained on, but narrow: another writing style cuts its top-1 accuracy from 94% to 63%, and heavy typos almost double its error. basal by Remigiusz Kinas ([Remek/basal-omni-pico](https://huggingface.co/Remek/basal-omni-pico), engine [rkinas/basal](https://github.com/rkinas/basal), Apache-2.0) is a local typed-decision model that returns a probability for each allowed answer; it loses less under shift and needs no examples, but it is much heavier.

| Hardware: AMD Ryzen 7 PRO 5850U laptop, 15 W, no GPU | Time per ticket, 16 threads / 4 cores | Memory |
|---|---|---|
| Local Scout | 0.45 ms / 0.45 ms | 0.13 GB |
| basal-omni-pico 0.8B Q8 (official basal engine, GGUF) | 2.0 s / 3.5 s | 1.8 GB |
| basal-2.0-mini 1.5B Q5 | 4.4 s / 8.7 s | 2.2 GB |
| basal-2.0-4.5B Q5 (only the 235-ticket subset, threshold from its 130-ticket validation: 60% / 8.5%) | 13.5 s / 25.3 s | 4.8 GB |
| Remote model in our recorded demo runs (OpenRouter free tier; not a provider benchmark) | 6–11 s per triage turn | — |

**Cascade (offline simulation from the recorded predictions, not in this release).** Local Scout answers when confident; otherwise basal pico; otherwise the model.

| CLINC150 test | Handled locally | Error among local answers |
|---|---|---|
| Local Scout alone | 45.6% | 5.9% |
| basal pico alone | 53.8% | 5.8% |
| Local Scout → basal pico → model | 54.7% (45.6% instant + 9.1% by basal) | 5.3% |

basal needs its own threshold, chosen on the validation tickets Scout passed on; with the shared threshold the cascade error rose to 7.5%. Under heavy typos the cascade error was 12.6%.

**Scout learning from basal (offline experiment, not in this release).** basal labelled a hidden pool of unlabelled CLINC training texts (0.5–3.1% label error at its validation-chosen threshold); Scout trained on its small human set plus the confident labels, with its threshold still chosen only on the fixed human-labelled validation set. Means of 3 draws that share the pool and test set:

| Human examples per category | Scout coverage | Scout top-1 | BANKING77 top-1 | Tickets passed to basal | Cascade handled locally |
|---|---|---|---|---|---|
| 5 | 24.7% | 81.8% | 50.4% | 75% | 51.3% |
| 5 + ~224 basal labels | 37.8% | 89.4% | 61.9% | 62% | 51.0% |
| 10 | 36.4% | 88.1% | 59.6% | 64% | 55.4% |
| 10 + ~175 basal labels | 42.4% | 91.4% | 63.0% | 58% | 51.8% |
| 25 (all human labels) | 45.3% | 94.3% | 63.3% | 55% | 54.4% |

Scout grows and fewer tickets need basal, but the cascade as a whole did not handle more (with 10 examples it handled fewer), and typos still raise the error. Pseudo-labels for "unknown" are not used when fitting Scout. In a release, tuning data would be collected locally in Scout's format, masked, under the `train` permission, and a new model would only replace the old one when the validation curve improves.

Data: CLINC150 (CC BY 3.0) and BANKING77 (CC BY 4.0), results only; typos are generated keyboard-style edits. CLINC150 and BANKING77 are standard public intent benchmarks written for research, not real support logs: expect lower numbers on longer, multi-issue tickets. The benchmark scripts are not part of the plugin.

## Four pillars

### Content safety

With profile-level `mask` consent, Scout masks recognized PII in tool and terminal results and flags suspected prompt injection while preserving the suspicious instruction as content. These are measured detector results, not guarantees of protection. PII precision (P) and recall (R) use same-type, one-to-one span overlap: a partial address match counts as a hit. The PII model was evaluated on Gretel test and the disjoint AdvPII reporting split.

| Type | Gretel P / R | AdvPII P / R |
|---|---:|---:|
| Email | 98.13% / 100.00% | 100.00% / 100.00% |
| Phone | 97.55% / 88.05% | 96.38% / 81.92% |
| Card | 97.86% / 96.38% | 100.00% / 94.55% |
| SSN | 98.87% / 94.40% | 90.96% / 95.57% |
| Address | 96.65% / 76.54% | 0.00% / unavailable |

AdvPII had no aligned positive address annotations; address predictions were false positives. On Gretel test, complete address coverage (every character of the gold address masked) is 75.2% in 1.7.3 after extending detected street lines over unit, postcode, city and state segments (35.8% before); false address alerts stayed at 0.02% of texts without addresses and 0.16% of AdvPII hard negatives. Hard-negative PII alerts occurred on 8/856 rows (0.93%); this sample does not establish a population false-positive rate.

Injection results use the release threshold raised by +2 logits from the validation threshold. Positives are recall; negatives are false-positive rates. TechQA counts documents. Small samples and the revealed reporting splits limit generalization.

| Dataset | Measure | Result |
|---|---|---:|
| deepset positive | recall | 34/60 (57%) |
| Gandalf | recall | 109/112 (97%) |
| deepset negative | false positives | 0/56 (0%) |
| CLINC | false positives | 4/5500 (0.07%) |
| BANKING77 | false positives | 0/3080 (0%) |
| TechQA | false positives | 5/245 (2%) |

deepset recall split by language (09.11): English 20/37 (54%), German 14/23 (61%); false positives 0/56. Most English misses are ordinary requests that deepset labels as injections relative to its own application ("Generate SQL code to access a database", "translate to polish", "Tell me a joke"). Scout targets instruction-override text inside tool results, so it does not flag plain task requests; widening the rules to catch them would raise false positives on documentation and code. Gandalf (explicit override attempts) is the closer match to that threat.

### Telemetry / Insights

Local views summarize tool operations, error codes, timing, PII alert categories, choices, training, handling, abstentions and observed token usage when available. Prompt masks are separated by source and injection alerts are counted separately. Missing usage is displayed as not observed with its mode or consent reason. Preview and export are explicit, local operations; see [INSIGHTS.md](INSIGHTS.md) for consent, scope and exclusions.

### Measured savings

CLI setup costs 0 agent tokens, but consumes local compute and disk. Scout's avoided-token and net figures are estimates based on assumptions and available usage, not measured provider savings. Paired benchmark (Hermes 0.23, 1.7.3, 09.11; 20 English triage messages — 14 in scope, 6 out of scope — each a separate `hermes -z` session; A = Scout off, B = Scout with operator CLI setup and local answers; measured provider usage, not estimates):

| Model (provider) | K | Main-call tokens A → B | Change | Incl. Hermes title calls | Accuracy A → B | Local answers (errors) |
|---|---|---|---|---|---|---|
| crouton-alpha (Nous)* | 1 | 52,802 → 42,560 | −19.4% | −13.5% | 16 → 16 /20 | 4 (0) |
| crouton-alpha (Nous)* | 2 | 52,823 → 42,560 | −19.4% | −14.3% | 15 → 16 /20 | 4 (0) |
| nemotron-3-ultra (OpenRouter) | 1 | 248,433 → 201,721 | −18.8% | −18.0% | 17 → 17 /20 | 4 (0) |
| nemotron-3-ultra (OpenRouter) | 2 | 248,369 → 201,793 | −18.8% | −18.0% | 16 → 17 /20 | 4 (0) |
| gpt-6.1-terra (Codex Responses), 1.7.0 | 1 | 300,909 → 304,702 | +1.3% | +1.2% | 16 → 16 /20 | 0 (local answers not supported for Codex Responses) |

\* `stealth/crouton-alpha` was later withdrawn by the provider.

On these 20 messages Scout answered 4 locally (all correct) and forwarded 16, so main-call savings track coverage: the ceiling at 4/20 is 20%, and the measured 18.8–19.4% is close to it. Hermes' own title calls are not affected, so the total falls 13–18%. On your data the saving is your coverage at the error limit you choose (`setup-triage` prints the validation curve); with Codex Responses there is none. With Codex Responses Scout only adds its tool descriptions (~190 tokens per call). Accuracy differences on forwarded messages reflect the changed context (Scout's tool/skill descriptions), not Scout decisions; N=20 and K=2 do not establish statistical significance. An earlier variant in which the agent called `scout_handle` for 20 texts inside one prompt *increased* tokens 24–28×: per-task tool calls in an agent loop cost more than they save.

### Reliability

| Evidence | Result | Scope |
|---|---|---|
| PII full-hook p95, 100k characters | 23.080 / 46.051 / 8.162 / 30.156 ms | Prose / mixed / dense / digits; warm process, fresh event IDs; dense content withheld |
| Injection full-hook p95, 100k characters | 57.043 ms | One synthetic input, earlier threshold; not a worst-case bound |
| Harness (offline) | 28 PASS / 0 FAIL / 5 BLOCKED per seed | Four seeds; the five lifecycle cases need a running Hermes |
| Harness + live Hermes 0.23 | 33 PASS / 0 FAIL / 0 BLOCKED per seed | Lifecycle cases T01 off/no state, T18 restart (private and public `scout_predict`), T22 report after the third event, T27 three clean profiles, T28 off/delete run in the official Hermes 0.23 image through Hermes' plugin loader, tool dispatcher and hook dispatcher, once per seed fixture; evidence is accepted only for the same plugin file tree (SHA-256 of all files), the same fixture, the matching step and Hermes 0.23 (`integ/`, mission tooling). No model calls; notifications are checked in state, not delivery to a chat UI |
| Unit tests | 236 OK; without a Hermes source tree the Hermes contract tests skip, and 4 converter tests need dataset tooling that is not shipped | 1.7.4 |
| Live Hermes | Validation passed (`--install-deps`, clean profile); prompt mask incl. names and the title channel, local answer with 0 main-call tokens, tool-result mask and alert observed | Synthetic fixtures, one provider (Nous) for live chat checks |

Speed on a laptop CPU (AMD Ryzen 7 PRO 5850U, one thread, no GPU): local classification median 21 ms (p95 26 ms, 40 calls) after a 0.6 s cold start; masking a 331-character e-mail with name detection median 0.8 ms. In the recorded demo runs the remote model took 6–11 s per triage turn (OpenRouter free tier; not a provider benchmark).

Measurements are from different test configurations and are not an end-to-end latency guarantee. A successful fixture does not establish general attack resistance.

## Datasets and licenses

The detector evaluations and training use Gretel synthetic PII, AdvPIIBench, deepset prompt-injections, Lakera Gandalf Ignore Instructions, CLINC150, BANKING77 and TechQA. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution, licenses and distribution caveats. Local Scout is MIT licensed; see [LICENSE](LICENSE).
