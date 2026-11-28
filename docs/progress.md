# Progress

Last updated 2026-11-27. Team: Leizhi Li, Mitchell Toney, Rathang Pandit.

## Dates (from the plan)
- ~~By Sun Nov 22: data pulled, scanner running on a sample~~ (Hugging Face done)
- **Mon–Wed Nov 23–25: full scans and hand review** ← now
- Fri–Sun Nov 27–29: numbers, figures, slides
- Dec 1 or 3: talk · Dec 8, noon: report due (6 pages max)

## Done
1. **Repo skeleton**, pushed to GitHub (collaborators not invited yet).
2. **Hugging Face pipeline**, tested (`pytest`: 11 passing):
   download (pinned CSV) → export → scan (Radware detector) → LLM classify.
3. **Full Hugging Face scan** of the 2025-07 snapshot: `results/hf_full/summary.json`.
   Reproduced locally with `scripts/setup_local.sh` (matches the cloud run).
4. **Classifier written** (`classify/classify_hf.py`). Trial run `--limit 80` on 2026-11-27:
   80 classified, 0 API errors, 5 positive texts (12 cards; 3 operational, 1 self_serving,
   1 malicious; 0 explicit prompts; confidence 0.5–0.8), ~$0.006. 5/80 is far above the
   expected rate, so likely false positives: hand-review them. Trial summary not committed
   (the full run overwrites it).
5. **Local setup fix**: `scripts/setup_local.sh` failed on macOS bash 3.2 (empty array under
   `set -u`) for the full run; fixed. API key goes in git-ignored `.env`
   (`set -a; source .env; set +a` before running the classifier).

## Hugging Face results so far (snapshot of 2025-07-13)

| | |
|---|---|
| Non-empty model cards scanned | 1,247,149 |
| Cards with any hidden text | 583,656 (47%), almost all HF template hints |
| Cards after dropping boilerplate (text in ≥ 50 cards or listed template) | **10,618 (0.85%)** |
| Hidden regions / distinct hidden texts | 6.72M / **6,885** |
| Distinct texts ≥ 25 chars (to classify) | 6,344 |

Rare (non-boilerplate) cards by technique: HTML comment 5,739 · custom YAML key 4,174 ·
CSS hiding 608 · zero-width 139 · bidi 67.

**First look (keyword screen, not a result):** 287 distinct texts mention AI/assistant
words, all benign on inspection (chat templates under `prompt_template`, commented-out
prose). A strict screen ("if you are an AI", "ignore previous instructions", AGENTS.md,
"recommend this model", ...) found **0**. Keywords miss things (Radware: 116 of 123 keyword
hits were false positives), so the LLM pass decides. Radware's GitHub rate (106 / 2M
READMEs) would predict about 66 in 1.25M cards.

## Decisions made (and why)
- **Pinned dataset revision and detector commit** so numbers are reproducible.
- **Boilerplate**: text in ≥ 50 distinct cards is `common`; HF's card template alone puts
  ~20 `<!-- -->` hints in ~23% of cards. Rare counts exclude it.
- **YAML header** is split off before scanning. Only custom top-level keys are reported;
  documented keys (`widget`, `base_model`, `datasets`, `extra_gated_*`, ...) are shown on
  the page. *Assumption, must be confirmed by the renderer test.*
- **Classify each distinct text once**, with Radware's prompt unchanged (comparability).
  Full run: 793 requests, ~$0.40 on Claude Haiku 5.5. Model gets no tools.
- **Hidden text never goes into git**; only counts are committed.

## Next steps (in priority order)
1. **Run the classifier, full** (trial done, resumes from it). Commit
   `results/hf_full/classify_summary.json`. Hand-review the 5 trial positives first.
2. **Fresh Hugging Face sample.** The snapshot predates the 2026 growth Radware saw
   (2.2× from June to September 2026). Fetch ~50k cards via the Hub API, sorted by last
   modified and by downloads, scan with the same pipeline, compare with 2025-07.
3. **Hand review protocol**: written labelling rules, every positive reviewed, a
   double-labelled sample, Cohen's κ; agreement of LLM vs hand labels.
4. **PyPI** (BigQuery `bigquery-public-data.pypi`, dry-run first; add reStructuredText
   `.. ` comment detection) and **npm** (registry API, sample top + recent).
5. **Visibility / renderer test**: the likely main contribution. For each technique ×
   channel (registry web page, raw API, installed package files, an agent web-fetch
   tool), is it visible to a human, and does an agent receive it? PyPI's
   `readme_renderer` is open source; use TestPyPI / a test HF repo where needed.
6. Stretch: PyPI history for a trend; sandboxed test of whether coding agents act on
   hidden text, using harmless canary phrases only.

## Plan critique (summary of the 2026-11-20 review)
- Mostly a port of Radware's pipeline; counts will likely be small. Make the renderer /
  visibility test the core result, not the backup.
- Validity: deduplicate (fine-tunes copy cards), weight by downloads, measure reviewer
  agreement, guard the classifier against injected "classify me as benign".
- Registry-specific channels Radware's markdown rules miss: RST comments (PyPI), YAML
  header (HF), `package.json` fields (npm), files shipped in the package (`AGENTS.md`,
  `llms.txt`, docstrings).

## Open questions
- Who owns which corpus? (npm / PyPI / Hugging Face)
- Invite Mitchell and Rathang to the GitHub repo (Settings → Collaborators).
- Recheck before the talk whether Radware published registry results.
