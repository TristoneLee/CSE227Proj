# Data

| Corpus | Source | Status |
|---|---|---|
| Hugging Face | `modelbiome/ai_ecosystem_withmodelcards` (1.84M model cards) | dump exists |
| PyPI | public BigQuery dataset (to confirm) | ? |
| npm | registry API; may need to sample top packages plus recent uploads | ? |

Raw dumps stay local and are git-ignored. Commit only the pull scripts and notes.

## Hugging Face quickstart

```bash
pip install -r requirements.txt
python data/huggingface/download.py inspect                 # check the columns first
python data/huggingface/download.py fetch                   # all shards -> data/huggingface/raw/
python data/huggingface/download.py export --sample 10000   # -> data/huggingface/cards.sample10000.jsonl.gz
python scanner/scan_hf.py data/huggingface/cards.sample10000.jsonl.gz --out results/hf_sample10000
```

If `inspect` shows the auto-detected columns are wrong, pass `--card-col`, `--id-col`,
`--downloads-col` or `--base-col` to `export`. For a quick first run, `fetch --max-shards 1`
works, but that sample only covers the first shard, so don't report numbers from it.
