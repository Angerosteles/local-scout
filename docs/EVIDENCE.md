# Evidence: measurements and how they were taken

Part of the [Local Scout README](../README.md).

## 1.8.0 frozen holdout

Measurement stages: blind run on the first release candidate → fix of English titled names → re-run on the release candidate; comparison: 1.7.5. All quality counts in this section come from the supplied `HOLDOUT_WYNIKI_18.md`, not the development sets or the historical results below.

The holdout was built by Gemini 4 Hydrogen without access to Local Scout's code and frozen on 14 November 2026: 1,503 synthetic texts, with 200 target values and 50 lookalike traps per category, plus 60 DE/FR/ES/IT texts. The stated target EN/PL split is 140/60, except PESEL 60/140 and SSN 180/20. Texts can contain multiple labelled mentions; mentions and texts are different denominators. The supplied summary identifies the frozen SHA-256 only by prefix `34cc60b3…`; obtain the full checksum and manifest with the holdout, published in a separate repository with the scoring script (link in the release notes), before reproducing it. The holdout itself is not shipped in the plugin.

The initial blind evaluation was made once on the first release candidate, with no threshold tuning on the holdout. It exposed a basic-mode English title bug: **10/94** titled mentions fully masked. The fix of English titled names raised this to **79/94**. The corrected English title result is **no longer blind**. According to the supplied results, every other reported count is identical between those two runs. Do not describe the fixed candidate as an untouched blind evaluation, or reuse this exposed set as a new blind test.

### Definitions

- **Masked:** every letter/digit in a labelled span is covered by the union of detected masks. Punctuation and whitespace do not count. This measures full value coverage, not same-type precision: another category's mask may contribute.
- **Partly:** at least one letter/digit is covered, including fully masked values. It is an inclusive count, not an additional disjoint group. A long address with only its street covered can be partly masked while still leaking information.
- **Traps:** labelled lookalikes that should remain visible under the holdout's annotation policy. A trap-text false alarm means at least one mask of that target type overlaps no labelled value. Other genuine labelled values can occur in the text.
- **Visible false masks:** detected spans overlapping no labelled value of any category. Rates are counts of these spans per 100 texts, not the percentage of texts affected and not the complement of precision. Several false masks can occur in one text. Counts use detector spans before rendering; overlapping or adjacent spans may be merged in the displayed mask.
- **Names:** each person mention is scored separately: full name, titled surname (title excluded from gold), reversed “Surname, Name”, or single given name/surname in person context. Company names, places, works and street patrons are not person-name gold. Addresses are full deliverable blocks under the labelling rules, not just street names.
- The `secret` evaluation groups secret values; it does not provide separate quality estimates for passwords, API keys, codes and PINs. CVV is not separately reported. The results measure local masking with names enabled, not Hermes hooks, injection resistance, optional basal confirmation or end-to-end provider payloads.

### Structured categories (measured with names basic, addresses on, other languages off)

| Category | 1.7.5 masked | 1.8.0 masked | 1.8.0 partly | Traps with a false mask of this type (1.7.5 → 1.8.0) |
|---|---|---|---|---|
| email | 200/200 (100%) | 200/200 (100%) | 200/200 | 26/50 → 26/50 |
| pesel | 200/200 (100%) | 200/200 (100%) | 200/200 | 0/50 → 0/50 |
| card | 195/200 (98%) | 195/200 (98%) | 195/200 | 4/50 → 4/50 |
| ssn | 197/200 (98%) | 197/200 (98%) | 197/200 | 0/50 → 0/50 |
| iban | 187/200 (94%) | 187/200 (94%) | 188/200 | 0/50 → 0/50 |
| phone | 106/200 (53%) | 165/200 (82%) | 166/200 | 4/50 → 6/50 |
| secret | 125/200 (62%) | 136/200 (68%) | 146/200 | 4/50 → 4/50 |
| address | 55/200 (28%) | 114/200 (57%) | 158/200 | 1/50 → 5/50 |

### Person names (all labelled mentions in EN and PL texts)

| | 1.7.5 | 1.8.0 basic | 1.8.0 deep |
|---|---|---|---|
| EN all names | 445/793 | 369/793 | 505/793 |
| EN full | 175/319 | 255/319 | 255/319 |
| EN titled | 56/94 | 79/94 | 79/94 |
| EN reversed | 19/71 | 27/71 | 32/71 |
| EN single | 195/309 | 8/309 | 139/309 |
| EN names unseen in earlier sets | 364/675 | 343/675 | 432/675 |
| EN visible false `[NAME]` (973 texts) | 229 in 973 texts (23.5/100) | 36 (3.7/100) | 82 (8.4/100) |
| PL all names | 119/410 | 176/410 | 247/410 |
| PL full | 37/191 | 112/191 | 112/191 |
| PL titled | 7/49 | 45/49 | 45/49 |
| PL reversed | 3/35 | 13/35 | 14/35 |
| PL single | 72/135 | 6/135 | 76/135 |
| PL names unseen in earlier sets | 109/390 | 176/390 | 236/390 |
| PL visible false `[NAME]` (470 texts) | 299 in 470 texts (63.6/100) | 28 (6.0/100) | 229 (48.7/100) |

| Name traps: 50 texts without a person | Texts with a false `[NAME]` | False name spans |
|---|---:|---:|
| 1.7.5 | 33/50 | 54 |
| 1.8.0 basic | 13/50 | 16 |
| 1.8.0 deep | 25/50 | 35 |

### All false masks over the whole holdout

Each setting uses all 1,503 texts. Type columns count visible false spans, not affected texts.

| Setting | All visible false spans | Per 100 texts | Name | E-mail | Secret | Address | Phone | Card |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.7.5 | 636 | 42.3 | 564 | 36 | 7 | 11 | 8 | 8 |
| 1.8.0 basic | 143 | 9.5 | 66 | 36 | 12 | 10 | 9 | 8 |
| 1.8.0 deep | 405 | 26.9 | 328 | 36 | 12 | 10 | 9 | 8 |

The two remaining 1.8.0 spans in each setting are one SSN span and one PIN span; the setup screen and `/scout details` count the PIN with password/PIN/key (13 false spans, 0.9 per 100 texts).

### Other languages

60 DE/FR/ES/IT texts; counts are fully masked values. The two 1.8.0 rows use basic names and addresses on.

| Setting | Names | Addresses | E-mail | IBAN | Card | Phone | Visible false `[NAME]` spans |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1.7.5 | 39/108 | 0/28 | 24/24 | 20/20 | 16/16 | 14/20 | 36 |
| 1.8.0 other_languages off (default) | 26/108 | 0/28 | 24/24 | 20/20 | 16/16 | 15/20 | 2 |
| 1.8.0 other_languages on | 40/108 | 0/28 | 24/24 | 20/20 | 16/16 | 15/20 | 5 |

### Seen and unseen names

“Unseen” means the complete labelled name string did not occur in the checked earlier synthetic evaluation sets. It does not establish absence from all training data. The EN/PL unseen rows above are directly reported. The seen rows below are arithmetic complements of the all-name and unseen rows in the same results file; no additional detector run was made.

| Earlier-set overlap | 1.7.5 fully masked | 1.8.0 basic fully masked | 1.8.0 deep fully masked |
|---|---:|---:|---:|
| EN seen | 81/118 | 26/118 | 73/118 |
| EN unseen | 364/675 | 343/675 | 432/675 |
| PL seen | 10/20 | 0/20 | 11/20 |
| PL unseen | 109/390 | 176/390 | 236/390 |

EN and PL text denominators are 973 and 470 respectively; the other-language set contains 60 texts. Structured-category EN/PL subtotals and a per-form partly-masked name table are not supplied in the results summary and are not inferred here. With `other_languages=off`, some foreign text remains uncertain and still reaches the fallback; the 26/108 name result is therefore not contradictory to the paragraph-level skip rule. Enabling the option did not recover any of the 28 international addresses.

## How detection works and how we measure it

### Detection mechanisms in 1.8.0

The pipeline normalizes supported Unicode representations while retaining offsets to original text, detects spans locally, resolves overlaps and substitutes category placeholders. A detector can both miss real data and mask lookalikes. Sources below are paths relative to the plugin.

| Category | Mechanism and limits |
|---|---|
| E-mail | Bounded local-part/domain regexes with an `@` precheck (`scout/privacy.py`). They recognize syntax, not whether a mailbox belongs to a person; this explains the technical-mail traps. Length bounds fix a backtracking problem also present in 1.7.5. |
| PESEL | Eleven digits and weighted checksum; calendar/date validation, with explicit-label and legacy-fixture exceptions for damaged dates (`privacy.spans`). It is not an identity check. |
| IBAN | Supported country-length table, alphanumeric syntax and mod-97 validation (`privacy._iban_valid`, `IBAN_LENGTHS`). Country coverage is finite. |
| Card | Numeric candidates, Luhn and contextual scoring; explicit payment/card labels can admit a candidate without a valid Luhn (`scout/pii_model.py`, `eligible`, `accepted`). CVV uses a separate explicit-label rule in `privacy.py`. A valid checksum alone does not prove that digits are a card. |
| SSN | Candidate format plus area/group/serial exclusions (000, 666, 9xx area; 00 group; 0000 serial), context model and explicit-label rules. The same category also includes NINO and another international identifier pattern (`pii_model.py`); the holdout row tests its annotated SSNs. |
| Phone | Bounded numeric candidates, recognized international country codes, length/format and local context, plus EN/PL rules and extensions. Direct phone labels/prefixes take precedence over selected ID/amount exclusions; date/IP checks and context rules reduce lookalike matches (`pii_model.py`). |
| Address | Bounded EN/PL street, postcode and locality patterns, explicit address labels, context scoring and PO Box patterns (`pii_model.py`). `privacy._extend_address` joins adjoining unit/postcode/city/state segments with a bounded extension loop and merges nearby address spans. This is incomplete international format support. |
| Secret / password / code / PIN | Known-format and prefixed-key patterns, labels, bearer/private-key patterns and split-key handling in `privacy.py`/`pii_model.py`; password-like scoring uses length, character classes, entropy, interleaving and nearby cues, with code/ID/URL/template exclusions. Digit codes/PINs require access-related cues. Source-specific low/high thresholds and the optional loopback basal second opinion are described in the historical [password method](#passwords-and-pins); the holdout does not isolate these subtypes or measure the optional engine. |
| Person name | The chain below combines token scores, span proposals, grammar features, document evidence and explicit filters. It runs only for user-role prompt text in request masking (`scout/prompt_guard.py`, `names=role == 'user'`); names intentionally stay readable in tool results. |

**PII context model.** `scout/pii_model.py` generates candidates for phone/card/SSN/address and computes a logistic probability from hashed shape, cue, nearby-word and validator features in `scout/models/pii_context.json`. Hard eligibility gates and direct-format/context rules can decide before the learned threshold. It is neither a checksum-only detector nor an LLM. The other-language option gates names and addresses, while structured categories run regardless of paragraph language.

**Name chain.** The previous detector (`scout/name_model.py`, `LegacyScore`) is a logistic token classifier using hashed character n-grams, casing, ordinary-word frequency and context. The EN route adds model C (`scout/name_model_c.py`): calibrated gradient-boosted trees proposing person spans and seeds. Its frozen trees read 62 of the original 584 features; runtime computes that projection, preserving float32 tree decisions. The G slot model (`scout/name_model_g.py`, shared `name_model_f.py`) scores a candidate's position and neighbouring words using shape, ordinary-word frequencies, punctuation and OEWN part-of-speech/semantic counts. The OEWN extract is ordinary grammar/animacy data, not a person-name gazetteer. C supplies multi-word additions; confirmed evidence can propagate repeats within the bounded text.

Singletons on the confident EN route require evidence such as a greeting/contact cue, title, signature, speaker line, person-verb context or membership in a detected full name. Context filters reduce false names in field labels, organizations and works. `basic` retains full names, titles, isolated reversed pairs and detected repeats; `deep` also retains supported singleton evidence. Outside confident EN, deep retains the previous detector; basic still filters it to the same basic-level contract. These are detection rules, not complete person recognition or identity resolution.

**Paragraph and document context.** `scout/paragraph_context.py` computes 32 shape/lexical features and returns language probabilities (EN/PL/mixed), document-style probabilities (email/chat, form/list, mixed, prose, technical) and normalized entropy. Today the name chain consumes its language probabilities; document-level name evidence also supports repeats. The type probabilities are available but do not enable a triage cascade, teacher loop or automatic training. They may support future triage work. `scout/language.py` combines lexical signals with this model to return `en`, `pl`, `other` or `uncertain`; “other” is conservative DE/FR/ES/IT function-word evidence, not a general multilingual classifier.

Confident EN means the largest language probability is EN and P(EN) ≥ 0.8, with lexical/Polish vetoes. PL, mixed and uncertain paragraphs retain the previous route. Text over 8192 characters or over 64 capitalized tokens bypasses the new name path. New-path failures fall back to the previous detector; basic-level and other-language filtering still follows. Baseline scan failures reach the ordinary fail-closed policy, rather than silently returning raw text. These name-path limits do not establish a whole-mask runtime bound. `other_languages=off` skips names and addresses only in paragraphs classified as other; `on` permits addresses and basic-level names there. Structured detection remains active in every language.

### Evaluation and change protocol

1. Build a synthetic holdout independently, without detector code, following explicit span rules for all categories. Include lookalike traps, multiple mentions, languages, formats and previously unseen names. Freeze inputs, labels and a full SHA-256 manifest before evaluation; publish them in a separate repository with the scoring script (link in the release notes).
2. Choose thresholds and rules using development/train/validation material only. Check complete name strings against earlier evaluation sets, retain an overlap manifest and report both all-name and unseen-name results. Earlier-set non-overlap is weaker than training-set non-overlap.
3. Run the frozen candidate once for the blind report. Preserve the candidate ID, settings, interpreter, command, checksum manifest and raw aggregate output. If the holdout exposes a bug, disclose the fix and any rerun, as for the English titles above. Further tuning needs a fresh independent holdout.
4. Report full and inclusive partial coverage with X/N denominators, trap texts with same-type false masks, and visible false spans per 100 texts separately. Do not compare the historical overlap-recall metric below directly with full value coverage. Check all categories, not just names, after each detector change; record intended differences and inspect losses and new false masks on development material.
5. Preserve differential parity when optimizing representation. The supplied commander's first-release-candidate record reports 0 differences over 2,952 corpus texts plus 20,000 random/adversarial texts in all four name/other-language settings, with 874,262 C vectors and maximum probability delta 0.0. This concerns compaction, not quality on unseen data. These long checks were not rerun for this documentation change; the earlier slimming report describes an earlier, incomplete stage.
6. Keep payload/integration checks separate: synthetic local spans do not prove Hermes hook coverage or injection blocking. Validate the final packaged tree with Hermes 0.23.0 and live synthetic requests, including user/system/tool/terminal/MCP/auxiliary paths, before updating the catalog pin. Injection timing/equivalence and long differential gates belong in the commander's separate validation mission.

**Measurement stages.** (1) Blind run on the first release candidate. (2) Fix of English titled names (10/94 → 79/94), re-run. (3) Removal of a small curated list of Polish given names and vocative forms (project rule: no name lists), re-run on the release: PL full names 123/191 → 112/191, PL all mentions 187 → 176 (basic) and 261 → 247 (deep), EN full names 256 → 255; false masks and all other categories unchanged. PL names seen in earlier sets fell from 2/20 to 0/20 in basic, consistent with the list having favoured familiar names. The tables show stage 3; stages 2–3 are not blind.
No new quality percentages are claimed for that change.

### Reproduction

The measurement helper is `holdout_pomiar.py`, supplied alongside the release evidence (not in the runtime package). Give it a parent containing `plugin/`, the frozen JSONL parts and the earlier-name manifest. It streams records and reports aggregates for basic/deep and both other-language settings; a 1.7.5 tree uses its original pipeline. It uses normalized local detector spans with names enabled and the candidate budget, without a registered basal engine. It does not run a Hermes provider request.

```sh
python -B holdout_pomiar.py /path/to/1.8-parent \
  /path/to/published-holdout/part-a.jsonl /path/to/published-holdout/part-b.jsonl \
  /path/to/published-holdout/part-c.jsonl \
  --seen /path/to/published-holdout/imiona_widziane_wczesniej.json > holdout-1.8.json
```

The part filenames above are illustrative; use the published manifest, verify its full SHA-256 and retain its actual filenames. Run the same helper in a fresh process against the 1.7.5 parent for comparison. Do not use the report's checksum prefix as a full verification hash. Full runs are reproduction work for a separate mission; no new quality run was made for this documentation update.

## Historical evidence (1.7.0–1.7.5)

Everything below is retained from earlier releases and tested configurations. It is not a measurement of the 1.8.0 defaults. Rows with an explicit version retain that version; otherwise these are pre-1.8.0 observations inherited from the 1.7.5 documentation. Historical percentages without X/N remain archival and are not used as 1.8.0 quality claims. The current frozen holdout and its full-value metric are above.

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

#### Passwords and PINs

Password-like values without a known key format are scored by shape (character classes, length 6–20, entropy, letters and digits interleaved) plus the nearest cue word (`password`, `pwd`, `pin`, `passcode`, `token`, `code`, `unlock` …, one typo tolerated). Values that miss common password rules (8+ characters, three character classes) lose 0.1 unless a strong cue is next to them. Fixed exclusions: URLs and domains, e-mails, paths, hashes and digests, UUIDs, MAC and IP addresses, phone numbers, dates and times, versions, advisories and ticket IDs, command-line flags, variables, file names, markdown links and HTML entities; keyword-argument calls, f-string and template fragments, regex groups, open assignments and rates are always excluded, and other code notation (calls, type annotations, indexing, operators, escapes, format strings, URL encoding) when a token has fewer than three kinds of symbols, because generated passwords pack many. A cue counts only within the current field or clause; JSON field names and words inside identifiers are not cues, and IDs with a simple shape need a direct cue. Product names (`Windows11`) and identifiers (`tempSensor_001`) count only next to a strong cue.

Digits-only values follow their norms: one-time and 2FA codes are 6 or 8 digits (TOTP/HOTP, RFC 6238/4226; SMS codes 4–8) and become `[CODE]` next to `verification`, `OTP`, `2FA`, `authenticator` … (or `login`, `security`, `SMS` … together with "code"); PINs are 4–6 digits after "PIN" and become `[PIN]`. A bare "code" is uncertain and left to the basal check; customs (HS), postal, error, status, promo, SKU and similar codes are never masked.

Thresholds (low, high) were chosen on the Gretel validation split: text and tool results 0.50/0.60, user prompts 0.45/0.55, terminal output 0.65/0.75. A score at or above high is masked; a score in the band is masked only when a registered basal engine confirms it (P ≥ 0.5, the candidate with up to 300 characters around it), and then the line above and below are asked too. At most 6 engine calls per text field and 8 s in total per tool result or provider request (each call's timeout is cut to the time left); on failure or after the budget, only high applies.

| Gretel test (not used for tuning) | Passwords | PINs | Tokens masked outside labelled entities |
|---|---|---|---|
| Before (key formats and `password:` labels only) | 13/59 | 0/2 | — |
| Shape, cues and norms, no basal | 50/59 | 1/2 | 3 in 5,000 documents, all unlabelled credentials (`otp_code` 543918, a verification code, `admin123`); another 4 overlap usernames written into `token` fields and SSN digits |
| With basal pico for the band | 52/59 | 1/2 | +1 token; 10 engine calls over 5,000 documents |

Negatives at the text threshold: TechQA 0 masked tokens in 1,506 texts; tool-call results from the Hermes function-calling dataset 11 in 1,100, each a credential on inspection (`Password: securePass123`, `"auth_token": "abc123token"`, `A new PIN '4321'`); Hermes' own source and documentation (356 files, used only as code-like negatives, nothing shipped) 2 tokens, one of them an example secret in its redaction docs. Before the false-positive pass the same code files gave 464 tokens and the Gretel validation split 32 tokens on IDs and usernames; rules for code notation, identifiers and field names removed them without lowering validation recall. Masking a 331-character e-mail with names went from a median 0.73 ms to about 0.9 ms; a synthetic 352 KB log adds about 0.2 s. On the CPU laptop basal pico takes about 1 s per question. Validation split for comparison: 56/64 passwords and 2/4 PINs without basal, before and after the false-positive pass. Limits: plain-word passphrases, values over 20 characters without a key format, short or simple values without a cue and digit codes without a cue are not detected; synthetic Gretel passwords are random strings, so real passwords made of words and years are easier to miss without a cue.

### Telemetry / Insights

Local views summarize tool operations, error codes, timing, PII alert categories, choices, training, handling, abstentions and observed token usage when available. Prompt masks are separated by source and injection alerts are counted separately. Missing usage is displayed as not observed with its mode or consent reason. Preview and export are explicit, local operations; see [INSIGHTS.md](../INSIGHTS.md) for consent, scope and exclusions.

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
| Unit tests | 300 OK; without a Hermes source tree the Hermes contract tests skip, and 4 converter tests need dataset tooling that is not shipped | 1.7.5 |
| Injection pattern timing | Hidden-content and exfiltration rules are answered from token positions instead of backtracking regexes (same matches as 1.7.4; an HTML or markdown comment that is never closed is now also checked). Measured 12.11.2026 on a laptop CPU, Python 3.14, on 256 KB single- and multi-line repeats of each opener and trigger: hidden-content and exfiltration scans at most about 70 ms each, slowest remaining regex about 0.3 s, full flag scan at most about 0.4 s. In 1.7.4 one hidden-content regex ran for more than 10 s on such input. These are observations on that input set, not a bound; the tests enforce 1 s per rule and 2 s per full scan, and tests/test_injection_equivalence.py compares match spans with the 1.7.4 regexes (window boundaries, nesting, random texts). Flags on deepset, Gandalf, CLINC, BANKING77 and TechQA unchanged (0 differences over 35,788 texts) | 1.7.5 |
| Live Hermes | Validation passed (`--install-deps`, clean profile); prompt mask incl. names and the title channel, local answer with 0 main-call tokens, tool-result mask and alert observed | Synthetic fixtures, one provider (Nous) for live chat checks |

Speed on a laptop CPU (AMD Ryzen 7 PRO 5850U, one thread, no GPU): local classification median 21 ms (p95 26 ms, 40 calls) after a 0.6 s cold start; masking a 331-character e-mail with name detection median 0.85–0.97 ms depending on the CPU core (0.73 ms before password-like detection). In the recorded demo runs the remote model took 6–11 s per triage turn (OpenRouter free tier; not a provider benchmark).

Measurements are from different test configurations and are not an end-to-end latency guarantee. A successful fixture does not establish general attack resistance.

## Datasets and licenses

The detector evaluations and training use Gretel synthetic PII, AdvPIIBench, deepset prompt-injections, Lakera Gandalf Ignore Instructions, CLINC150, BANKING77 and TechQA; the Hermes function-calling dataset only for a password false-positive count. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for attribution, licenses and distribution caveats. Local Scout is MIT licensed; see [LICENSE](../LICENSE).

