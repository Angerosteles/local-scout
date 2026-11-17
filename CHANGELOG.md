# Changelog

## 1.9.0 — 2026-11-17

- Optional numbered masks for names and contacts, off by default. Equal exact values share tokens within a category, profile and process. Nothing is restored; the map stores salted digests, not plaintext.
- Tighter phone, secret, card, IBAN, SSN and address detection: national phone formats and extensions, secret labels and keys in prose, Amex grouping, more IBAN country lengths, SSN list formats and international address blocks.
- Normalize supported HTML entities, URL escapes, homoglyphs, line breaks and separators. Coverage is incomplete; base64, digits written as words and spaced letters remain unsupported.
- Fewer false masks for technical tokens, register dumps, dates, labels and version tables. Blind holdout v2: 499 false spans per 2,400 texts in 1.8.0 → 491/2,400 with basic names. One new false secret mask occurs in an English trap; main-set name results are unchanged. Deep loses one single-name side mention in the separate HTML-entity test (3/3 → 2/3).
- Faster masking: the development benchmark measured mean 2.69 → 1.61 ms (about 0.6× the 1.8.0 time), p95 6.55 → 3.78 ms on the same machine. This is historical benchmark evidence, not a new timing run for this wrap.
- Menu, `/scout`, README and EVIDENCE now use blind holdout v2 (2,400 synthetic texts) with per-category denominators. The older holdout and Gretel are disclosed as development sets. Shorter Numbered masks help fits an 80-column screen and reflows at 40.
- EVIDENCE compares 1.9.0 with Microsoft Presidio (default English configuration) on the English part of the blind holdout: Local Scout masks more structured values and addresses with fewer false masks and is faster; Presidio is clearly better on English person names. See [comparison](docs/EVIDENCE.md#comparison-with-microsoft-presidio).

See [release evidence and limits](docs/EVIDENCE.md#blind-holdout-v2-190).
