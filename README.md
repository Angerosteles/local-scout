# Local Scout — local privacy for everyday agent work


Local Scout is a local privacy layer for Hermes Agent, built for people who handle customer and co-worker data at work: support tickets, inboxes and internal documents. Hermes already redacts secrets such as API keys in file and terminal output. Scout adds masking for **detected personal data and password-like values** in the content the model reads, plus prompt-injection flags and optional local ticket triage.

The practical idea: the model can understand an overdue invoice without receiving the customer's detected e-mail address or card number. Detectors make mistakes; Scout reduces exposure within supported text paths, rather than making the agent a secure sandbox.

## See what the model receives

In this live Discord test, the user typed an e-mail address and card number. The recorded provider request contained `Hi, I'm [NAME], [EMAIL], card [CARD]`. Hermes then read a mailbox containing a phishing instruction to collect card numbers and send them away. Scout masked the detected values and flagged the instruction. Asked where the cards were supposed to go, the bot quoted: **`send them to [EMAIL]`**.

<p align="center"><img src="docs/discord-demo.png" width="620" alt="Live Discord demo: synthetic personal data entered by the user, a phishing message identified, and its destination quoted as send them to [EMAIL]."></p>

The data is synthetic; the Discord session and the model calls (DeepSeek V4.1 Flash through OpenRouter) were real. This shows one observed payload and response, not general attack resistance. The bot's refusal is model behaviour; injection is **flagged, not blocked**. [Gateway evidence and caveats](docs/SECURITY.md#gateways).

[Observed Hermes-alone / Scout comparison](docs/EVIDENCE.md#what-hermes-already-does-and-what-local-scout-adds): synthetic live cases, not detection-rate or security guarantees.

## How it works

Enabling the plugin in Hermes enables local masking of detected structured data and password-like values on a fresh profile, in Hermes' request middleware and tool-output hooks. The in-memory defaults are `mode=report`, profile grants `observe`, `report` and `mask`, names off, addresses on, other languages off, and Insights, triage and learning off. Scout does not store prompt contents or detected values; explicit setting changes are saved locally, and counters only with Insights on (legacy `observe` grants count as Insights on unless disabled). Plugin, masking and name masking are three separate choices: `/scout masking off` is a lasting opt-out, and changing names while masking is off does not turn masking on.

Scout replaces detected e-mails, phones, cards and CVVs, IBANs, PESEL and SSN numbers, addresses, API keys and other secrets with placeholders. **Passwords, 2FA codes and PINs without a known format** (`use IkXzYx33@$`, `Your verification code is 482913`) become `[SECRET]`, `[CODE]` and `[PIN]`, judged by their shape, nearby cue words and the usual norms, while URLs, IDs and code stay readable. An optional local basal engine gives a second opinion on uncertain ones ([method and limits](docs/EVIDENCE.md#passwords-and-pins)). Person-name masking applies to user prompts. Supported paths include file and terminal results, MCP tool results, conversation text, system instructions and tool-call arguments; auxiliary calls such as session titles receive best-effort masking too. No network calls (an optional basal engine is reached on loopback only), no GPU.

**Masking is one-way.** Placeholders are not restored in replies or outgoing arguments. The model cannot address an e-mail using `[EMAIL]`. Scout registers the `masking` skill when supported. With profile masking enabled, it also adds a short system-prompt section for new sessions. The guidance says to preserve masks, avoid guessing originals, distinguish people by role, and ask for local completion. These are instructions to the model, not reversible substitution. Optional numbered masks in 1.9 remain one-way; nothing is restored.

Customer, order and ticket IDs, dates, e-mail subjects and **person names in tool results** stay readable. Work by ID, then look up the address in your own system. Source files remain unchanged, and Scout does not prevent an agent from transferring a file it can read. [Data handling and security scope](docs/SECURITY.md).

## What leaves the device

Hermes sends the resulting request to its configured model provider. This table
assumes Scout masking is on; detection is incomplete and values it misses leave
unchanged. With masking off, all content passes unchanged. Names are off by default.

| Data | User prompt | Tool / MCP result |
|---|---|---|
| E-mail, PESEL/SSN IDs, IBAN, card | Detected values masked | Detected values masked |
| Phone / street address | Detected values masked; addresses can be off | Same address/language switches |
| Secrets / codes / PINs | Detected values masked | Detected values masked |
| EN names | Unchanged by default; basic/deep opt-in | Unchanged |
| PL names | Unchanged by default; basic/deep opt-in, incomplete | Unchanged |
| Names / addresses in other languages | Names require opt-in; both skipped in recognized foreign paragraphs unless other_languages on | Names unchanged; addresses use the same language switch |
| Images and other non-text content | Passed through unscanned | Passed through unscanned |

Customer/order/ticket IDs and dates stay readable. Discord thread titles may
contain a name even with user-name masking on. Optional basal on loopback receives
unmasked candidate lines; it is a separate local recipient, not the provider.

## Name, address and language options

Name masking is optional (default `off`) and applies only to user-role prompt text; names in tool results stay readable so the model can sort mail and documents by person. `/scout names basic` masks full names, titles with a surname, repeats and “Surname, Name” pairs; `deep` also masks single names with contextual evidence and is noisy on Polish text (≈49 false names per 100 PL texts). Addresses are on by default. Recognized DE/FR/ES/IT paragraphs skip names and addresses unless `other_languages` is on; structured data runs in every language, and short foreign text can remain uncertain. Names are off by default because structured data is where leaks do concrete harm, while names are often what the user wants the model to see.

See [all options, English routing and migration from 1.7.5](docs/CONFIGURATION.md#masking-options-and-migration-from-175).

## What ships, and what is still experimental

- **Masking — the core.** Local detectors and placeholders on supported Hermes text paths, including MCP output. Enabled by default when the plugin is enabled on a fresh profile, independently of opt-in triage.
- **Injection flags — in this release.** Suspicious instructions remain visible as content, with an alert for the model. Detection has misses and false alarms.
- **Local triage — optional, needs calibration.** A classifier trained on your labelled tickets classifies routine tickets locally and returns the category label as the reply, with **0 main-call tokens**; uncertain cases go to the model. Hermes title calls may still run. The shortcut supports chat completions; Codex Responses always forwards to the model. Writing-style changes and typos can invalidate a fitted threshold.
- **basal — optional local engine.** Zero-shot triage through a basal engine you install and run on loopback, with its threshold calibrated on your validation data. It takes seconds per ticket, so in-turn local answers forward rather than wait. The same engine can confirm uncertain password candidates during masking (at most 8 s in total per tool result or provider request; after that, or if it fails, only the deterministic threshold applies). The triage cascade and learning-from-basal results are offline experiments outside this release.
- **Insights — in this release.** Local summaries of masking categories and sources, injection alerts, local answers, forwarding reasons and observed usage. Never the text itself; exports are explicit and local.
- **Context guard — branch only.** The `context-guard` branch experiments with asking for human approval of suspicious outgoing calls using session context. **It is not part of this release.** It has known misses and false alarms. [Branch results and limitations](docs/CONTEXT_GUARD.md).

## Quick start

Requires Hermes `>=0.23.0,<0.24.0` and Python `>=3.11,<3.15`, on Linux or macOS.

```sh
hermes plugins install Angerosteles/local-scout --enable
alias scout="bash ~/.hermes/plugins/local-scout/scout.sh"
scout
```

`scout` (or `hermes scout`) opens the setup screen: arrows or j/k select, Enter/Space change, `s` saves, `q`/Esc ask before discarding changes. Saved settings apply from the next message; only the masking system-prompt section needs a new session. In chat, `/scout` shows status, `/scout details` the measurement table, and `/scout names basic` or `/scout masking off` changes one setting; changes are accepted only from a local session, never from messaging gateways, and these commands are not model tools. Add the alias to `~/.bashrc` or `~/.zshrc`; `HERMES_HOME=/path/to/profile scout` selects another profile. [Terminal fallback, profiles and commands](docs/CONFIGURATION.md).

Optional Insights and triage setup for scripts (basic masking already works without these commands):

```sh
scout configure --mode report --dataset profile --approve observe,report,insights,mask
scout setup-triage --history my_tickets.jsonl --marker "Customer message:" --mask-prompts
scout insights
scout configure --mode off
```

`--approve` replaces the whole grant map, so list every scope you want. `setup-triage` changes the mode to `on_demand` and also enables local answers; it does not keep the preceding `report` mode. `--mode off` disables Scout; retained data remains. For triage, use labelled examples without personal information and review the validation coverage/error curve. [Setup, profiles and local-answer configuration](docs/CONFIGURATION.md).

## Evidence you can inspect

**Blind synthetic holdout v2: 2,400 texts**, independently built without detector code, frozen 15 November and measured once on 17 November 2026 against 1.8.0 and 1.9.0. Counts are fully masked target values, with trap false alarms separate. The test uses basic names, addresses on and other languages off; names are off by default in the product. These are results on our own synthetic set, not protection guarantees: expect lower detection on real data, and 100% here means every test value, not every possible value. The full holdout v2 will be published with Local Scout 2.0.

On the English part of the same texts, Microsoft Presidio in its default configuration masks fewer structured values and addresses with more false masks and has no secret detection, but is clearly better on person names ([comparison](docs/EVIDENCE.md#comparison-with-microsoft-presidio)).

| Category | 1.8.0 fully masked | 1.9.0 fully masked | 1.9.0 partly (inclusive) | Trap texts with this false mask, 1.8.0 → 1.9.0 |
| --- | --- | --- | --- | --- |
| email | 200/200 | 200/200 | 200/200 | 14/50 → 14/50 |
| pesel | 200/230 | 212/230 | 212/230 | 0/50 → 0/50 |
| card | 199/200 | 200/200 | 200/200 | 0/50 → 0/50 |
| ssn | 199/230 | 209/230 | 209/230 | 0/50 → 0/50 |
| iban | 200/200 | 200/200 | 200/200 | 0/50 → 0/50 |
| phone | 230/230 | 230/230 | 230/230 | 0/50 → 0/50 |
| secret | 175/230 | 199/230 | 204/230 | 0/50 → 1/50 |
| address | 158/230 | 199/230 | 228/230 | 1/50 → 1/50 |
| name | 166/300 | 166/300 | 168/300 | 8/50 → 8/50 |

Names are unchanged: basic fully masks EN full names 233/272 and PL full names 90/132; all mentions 462/827 EN and 162/412 PL. Deep masks 579/827 EN and 216/412 PL mentions. False names: basic 252/1,582 EN texts and 178/758 PL texts; deep 310/1,582 and 795/758. Deep is especially noisy on Polish text.

With basic names, all false spans fall 499/2,400 → 491/2,400 texts, but secrets gain one false mask in an English trap. International addresses rise 0/12 → 10/12 in 60 DE/FR/ES/IT texts. In the separate 210-text obfuscation test, HTML/URL normalization improves; base64, digits written as words and spaced letters remain unsupported. Deep loses one single-name mention in the HTML-entity group (3/3 → 2/3). The old 1,503-text holdout and Gretel were used to tune 1.9 and are **development sets, not blind evidence**. [Tables, definitions and protocol](docs/EVIDENCE.md#blind-holdout-v2-190), [how detection works](docs/EVIDENCE.md#how-detection-works-and-how-we-measure-it), [1.9.0 changes](CHANGELOG.md#190--2026-11-17).

## Limits and disclosures

- English and Polish formats are supported to different degrees, including US-first and some Polish phone and address formats. Coverage is incomplete. Dates, including dates of birth, remain readable. Images and other non-text content are passed through unscanned, with an `unscanned=image` (or audio/file/other type) alert on supported user/tool paths. Text blocks in the same message are still masked; text scan failures and oversized text still follow `guard_mode`.
- Passwords are recognised by shape and cues, not meaning: plain-word passphrases (`correct horse battery staple`), values over 20 characters without a known key format, short or simple values without a cue and digit codes without a word such as "PIN", "OTP" or "verification" stay visible, and in terminal output a value needs a cue. Some IDs and usernames that look like passwords are masked.
- Numeric detection combines validators and context: cards pass Luhn unless an explicit payment/card label admits them; IBANs use supported country lengths and mod 97; PESEL uses its checksum and calendar validation, with explicit-label and legacy-fixture exceptions for damaged dates. SSNs exclude invalid area/group/serial values (including 000, 666 or 9xx area). This keeps order and tracking numbers readable, but made-up test values such as `1111 0000 4444 5555` stay visible. For tests, use valid samples such as card `4111 1111 1111 1111` or IBAN `DE89 3704 0044 0532 0130 00`.
- Compatible Hermes middleware and an enabled mode with profile masking permission are required (enabled by default on a fresh profile). Scan failures withhold user content by default; system prompts are kept (data split across text blocks is merged into one masked block), and only a failure of the whole masking step replaces the request (fail-closed); `scout configure --mode report --guard-mode open` is the explicit, per-profile fail-open option. If a Hermes middleware call itself fails, Hermes continues with the original request, and safe-worker mode can bypass plugins. Auxiliary masking is best effort: verify outgoing payloads for your deployment.
- On Discord, Hermes names a new thread after its first message and copies that title into the system prompt. Detected values are masked there too, but person names are not. [Operational caveats](docs/SECURITY.md#auxiliary-calls-and-prompt-privacy-hermes-023).
- Scout makes no network calls except optional basal requests to loopback for triage and uncertain password candidates (no proxies, no redirects). The basal engine receives unmasked candidate lines and, for a confirmed candidate, neighbouring lines. It reads content Hermes passes to plugin hooks and middleware, the selected profile's `config.yaml` (title settings), and history files you name. State stays in its plugin-data directory (the `scout` launcher adds one link under `~/.cache/local-scout`). It runs no shell commands, starts no background processes and stores no provider credentials. Local training artifacts can retain vocabulary; protect them separately from the narrower Insights export. [Full disclosures](docs/SECURITY.md#disclosures).

## What's next?

Numbered masks in 1.9 are optional and one-way; restoration is outside this release. The triage cascade, teacher loop and multimodal work are experiments outside 1.9.0; see [historical evidence](docs/EVIDENCE.md#triage-on-real-data-basal-and-the-cascade-measured-offline).

## How it was built

Local Scout was designed and directed by The Loose Goose (Angerosteles on GitHub): the idea, the product decisions, the quality bar, the password cascade, testing in Hermes and the demo. It was built with AI assistance. Claude (Anthropic) wrote most of the code, tests and documentation and coordinated the work. OpenAI models (through Codex and ChatGPT) and Google Gemini did independent reviews, bug hunts and experiments; "Sol", "Terra" and "Argon" in the evidence are these AI reviewers. All measurements were run locally on the published code.

Local Scout is [MIT licensed](LICENSE). Thanks to Remigiusz Kinas for basal; dataset, dependency and optional-model attributions are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Numbered masks (1.9, default off)

Use `/scout numbered on` in a local terminal, the **Numbered masks** setup row, or `hermes scout configure --mode report --pseudonyms on`. NAME, EMAIL, PHONE and ADDRESS use tokens such as `[NAME_1]` and `[EMAIL_1]` for the same detected regions. Equal tokens mean equal exact values within a category, profile and process; different spellings remain separate and numbers in different categories are unrelated. Regions overlapping secrets or other sensitive categories keep plain masks. Nothing is restored in replies, tool arguments, files or history; streaming is unchanged. This supports grouping and drafts, not resolving real recipients. See [limits and restart behavior](docs/CONFIGURATION.md#numbered-masks).
