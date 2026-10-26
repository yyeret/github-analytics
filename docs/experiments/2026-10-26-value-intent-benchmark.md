# Value-intent benchmark: public PRs and preventive-work controls

This benchmark tests whether the scan recognizes and consistently scores **intended contribution** from a PR description and repository context. It does not test actual value realization, factual verification of PR claims, or implementation risk.

The reference labels are **agent-provisional judgments, not human ground truth**. They were drafted without opening the earlier study's model-result artifacts, but the labeling agent inherited earlier study discussion. This is not a blinded labeling study. Human review of the cases and accepted score buckets is required before claiming validated accuracy.

## Frozen inputs and references

[public_prs.json](../../tests/fixtures/value_intent/public_prs.json) contains 40 actual public PRs, 20 per repository, and seven separately marked synthetic controls. The descriptions are exactly their first 1,500 characters, not generated summaries. URLs, titles, original lengths, truncation flags, capture time and available head SHAs accompany them. Titles are retained for identification; the application controls which fields reach Jev.

- **Spec Kit:** first 20 PRs in ascending PR-number order from the captured October 26 study sample. This reuses public source material, not previous classifications. Original head SHAs were unavailable in that saved sample and remain explicitly null.
- **FastAPI:** first 20 from the public REST pull-request endpoint with `state=closed&sort=updated&direction=desc&per_page=30`, captured October 26, 2026. These are closed PRs, not a claim that all were merged. All 20 have captured head SHAs. The fixture deliberately retains the untouched template and the title/body mismatch encountered in this mechanical sample.
- **Holdout:** before writing reference labels, five cases per repository were frozen by choosing the lowest SHA-256 hashes of `value-intent-holdout-v1:<repository>#<number>`. There are 30 development and 10 held-out cases. Do not use held-out disagreements to tune prompts or accepted buckets; retain them for a subsequent assessment.
- **Controls:** cosmetic change, vague description, untouched template, blank description, terse 2FA, preventive chaos experiments and shorter CI feedback. These use a clearly synthetic developer-platform context and never count as public PR observations.

Reference judgments identify whether a description is assessable, specify acceptable Fibonacci buckets for each dimension and explain the reasoning. Neighboring buckets acknowledge coarse ordinal judgment. `time_criticality=1` means no temporal urgency intent is discernible in this input; it does not establish that the real work is timeless. A dependency security warning has broader acceptable timing buckets because local applicability is unspecified.

The frozen contexts are manual agent syntheses of the actual [Spec Kit README](https://github.com/github/spec-kit/blob/main/README.md), [preset reference](https://github.com/github/spec-kit/blob/main/docs/reference/presets.md), [FastAPI README](https://github.com/fastapi/fastapi/blob/master/README.md) and [feature reference](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/features.md), read on the capture date. They are not official strategic roadmaps. Keeping them explicit separates a rubric comparison from an experiment in richer LLM-researched context. A separately saved context override can run the latter arm without changing the fixture labels.

Public acquisition for this fixture used five reads: one GitHub PR-list request and four raw documentation requests. No private data or paid model calls were used to construct the fixture. The earlier Spec Kit source capture is reused and is not included in that count.

## Running and interpreting the evaluator

Use the repository's existing Python environment on macOS or Windows. Offline analysis needs no credentials or network access:

```bash
python scripts/evaluate_value_intent.py --results baseline.json candidate.json
```

Each input is a checkpoint from this CLI, containing `records` with `case_id` and the actual application `result`. A result can be assessed, an `insufficient_context` abstention, or a provider failure. The evaluator reports these separately, together with missing cases; provider failures are never credited as correct abstentions. Results report overall, repository and development/held-out partitions. Synthetic controls appear separately when selected.

The outputs include assessment coverage, coverage of expected-assessable cases, correct and unexpected abstentions, inappropriate scoring of indeterminate inputs, agreement with accepted buckets and disagreements more than one rubric level away from the closest accepted bucket. The denominator for dimension agreement includes only valid dimensions on assessed, expected-assessable cases. Inspect coverage and failures alongside agreement: a classifier cannot earn a useful verdict by abstaining on difficult work. Totals and rank order are deliberately not treated as ground truth.

Live calls require explicit `--live` and a Jev key already exported as `JEV_API_KEY` (preferred), `TYPESAFE_API_KEY`, `TYPESAFE_API_TOKEN` or `typesafe_api_token`. Retrieve it with the existing secret-manager workflow; do not put keys into CLI arguments or JSON. No Gemini calls or repository research run implicitly:

```bash
python scripts/evaluate_value_intent.py --live --output candidate.json --include-controls --budget 47
python scripts/evaluate_value_intent.py --live --output candidate.json --include-controls --budget 47 --resume
python scripts/evaluate_value_intent.py --results candidate.json --include-controls
```

`--limit` limits selected cases; `--budget` is a hard ceiling of 60 assessment attempts, including previous attempts in the same resumed checkpoint. `--partition development` or `--partition held_out` selects a frozen partition. Use identical selections when resuming. To compare researched context, save a JSON object mapping repository names to application context objects, then supply `--contexts researched-contexts.json` with a **new** output path. Overrides merge into the frozen contexts so the synthetic control context remains available. Preserve both arms rather than overwriting baseline results.

The runner calls the actual `product_value.assess_pr` implementation serially. It fingerprints description, title/identity, truncation metadata, exact context and the application module's source hash. Resume rejects changed inputs or rubric logic before making any calls. A checkpoint is written before and after each attempt. Interrupted attempts conservatively consume budget and are not silently repeated; review those entries before starting a separately named run. Keys and exception messages are never persisted.

New checkpoints embed the public repository contexts. Offline evaluation checks the manifest and every result fingerprint before crediting scores. An explicit `--contexts` override must match the original inference context. Older checkpoints without embedded contexts require their original context through `--contexts` when they differ from the fixture defaults. Changed descriptions or contexts with the same case IDs are rejected. Different context arms can be evaluated independently using their embedded profiles.

The earlier Spec Kit study can supply baseline results for its matching 20 public cases without additional paid calls. Compare that subset with the same candidate cases; its context and rubric both differ from the candidate, so any change is a combined-change observation, not causal evidence that deeper context alone improved classification. FastAPI and synthetic cases have no historical baseline in that study.

To preserve the production truncation contract, a captured 1,500-character prefix marked truncated receives one trailing space before `assess_pr` computes its truncation flag. The classifier still receives exactly the captured prefix. This is metadata reconstruction, not invented missing content.

Focused automated tests establish metric calculation, provider-failure versus abstention accounting, fingerprint invalidation in live and offline paths, request ceilings, resumability and truncation reconstruction. They do not establish Jev accuracy. Before using agreement as a product-quality measure, have a maintainer review the reference rationales and a fresh reviewer label the held-out cases independently.

## October 26 live validation

Two live researched profiles were generated from ten public sources each. The initial frozen run completed 47 cases (46 actual Jev requests; blank input skips the provider), with 39 assessed, eight abstentions and zero provider failures. Four abstentions matched indeterminate references; four routine FastAPI release preparations were unexpectedly rejected. All four were development cases.

The assessability question conflated substantive contribution with identifiability. A development-only correction now recognizes concrete routine actions, including release preparation. A separate follow-up on those four cases plus seven controls assessed all four releases at total 4, kept vague/template/blank inputs unscored, and recognized terse 2FA, preventive chaos and CI feedback contributions. No held-out disagreement was used to tune that correction. Original and follow-up runs retain separate fingerprints; these are not silently merged into a final benchmark.

On the initial held-out partition,36/40 dimension selections matched provisional acceptable buckets, with one disagreement more than one rubric level away. That is draft-reference agreement, not 90% validated accuracy. On 20 matching Spec Kit cases, historical agreement moved 67/80 → 68/80 dimensions while coarse disagreements moved 1 → 2. Both context and rubric changed, so this does not isolate deeper context or establish a broad accuracy gain. CI feedbackPR #5051 changed from value1,total 4 to value 3, total 7.

A local `POST /api/analyze` proof assessed three saved public PR descriptions using live GitHub repository research, Gemini and Jev. It completed 3/3 in 6.34 seconds; this did not verify fresh collection or the deployed Workers runtime. Raw key-free inputs, provider responses, context profiles and summaries are retained in shared project memory under `reports/2026-10-26-value-intent-research/`. Overall task usage through that proof: 59 Jev, 11 Gemini and 50 GitHub requests. Subsequent review fixes reserve optional-research time and validate offline inputs; the full live benchmark was not rerun after those fixes.
