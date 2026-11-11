# Third-party notices

Local Scout is MIT licensed; see [LICENSE](LICENSE).

## Datasets and model attribution

The following attributions and license designations are summaries. Consult the original dataset distributions for complete copyright notices, license texts, attribution requirements and any changes before redistribution; these summaries are not substitutes for those notices.

| Dataset / provider | Use | Reported license |
|---|---|---|
| Gretel synthetic PII dataset, Gretel | PII and person-name model training on validation and evaluation on test (`models/name_model.json` weights are derived from it) | Apache License 2.0 |
| AdvPIIBench (AdvPII) | PII model training and disjoint reporting split | Creative Commons Attribution 4.0 International (CC BY 4.0) |
| deepset/prompt-injections, deepset | Injection classifier training and evaluation | Apache License 2.0 |
| Gandalf Ignore Instructions, Lakera | Injection classifier training and evaluation | MIT |
| CLINC150 | Injection classifier training and evaluation; negative texts for the person-name model (lowercase word frequencies) and for false-positive measurements | Creative Commons Attribution 3.0 (CC BY 3.0) |
| BANKING77 | Injection classifier training and evaluation; negative texts for the person-name model (lowercase word frequencies) and for false-positive measurements | Creative Commons Attribution 4.0 International (CC BY 4.0) |
| TechQA | Injection classifier training and evaluation; negative texts for the person-name model (lowercase word frequencies) and for false-positive measurements | Apache License 2.0 |

The injection and PII-context artifacts contain hashed feature coefficients, not dataset text. The person-name model (`models/name_model.json`) contains hashed feature coefficients plus a list of 3,097 common lowercase English words with their counts, derived from TechQA, BANKING77 and CLINC150 texts; it contains no sentences or records. These artifacts are supplied under the plugin's MIT license subject to the dataset obligations above (attribution for the CC BY datasets). Original dataset texts are not supplied here; retain these attributions when redistributing the artifacts. Licenses of Gretel, TechQA, CLINC150, BANKING77 and AdvPIIBench were checked on the publishers' pages on 2026-11-07; the deepset and Gandalf designations are taken from their dataset cards.

## Dependencies and optional models

Libraries are separate dependencies and are not bundled: NumPy, SciPy, scikit-learn, joblib and threadpoolctl use BSD-family licenses; PyYAML uses MIT. Optional PyTorch uses BSD-3-Clause with separate component licenses. PyArrow, used in PII model development, uses Apache-2.0. Distributions must include applicable notices from the actual packages and transitive dependencies; lockfiles do not replace their license texts.

Thanks to Remigiusz Kinas (Remek) for the basal decision models and the basal engine (basal-omni-pico on Qwen3.5-0.8B, basal-2.0-mini on Bielik 1.5B v3, basal-2.0-4.5B), reported as Apache-2.0. Weights are neither distributed nor automatically downloaded; check the chosen GGUF release and base-model licenses. The engine is not bundled: the optional `basal_zero_shot` method talks to a basal engine that the user installs and runs locally (loopback only). The offline measurements in the README and our tests of that method used the official engine (basal 2.8.0, GGUF mode).

MiniCPM-V, LFM2.5 and LFM3 recipe cards describe optional plans; availability, formats and commercial terms were not verified. They are not runtime dependencies.
