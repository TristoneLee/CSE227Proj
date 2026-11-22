# Scanner

Hidden-text detection. It uses Radware's deterministic detector `ipiscan.detect` (MIT),
pinned in `requirements.txt`, plus a wrapper for each registry.

## Hugging Face: `scan_hf.py`

```bash
python scanner/scan_hf.py data/huggingface/cards.sample10000.jsonl.gz --out results/hf_sample10000
```

- Techniques: HTML comments, white LaTeX, CSS hiding, invisible Unicode (Radware's README set) plus their noise filter.
- The YAML header is split off before scanning. `--yaml` also reports long text inside it (experimental, off by default so numbers stay comparable with Radware).
- **Boilerplate:** the HF card template is full of `<!-- ... -->` hints and fine-tunes copy base cards.
  Text found in ≥ `--common` cards (default 50) is marked `common`; strings listed in
  `hf_templates.txt` are marked `template`. The "rare" counts leave both out.
  Grow `hf_templates.txt` by reviewing `most_common.jsonl` by hand.

Output in `--out`:

| File | Contents | In git? |
|---|---|---|
| `summary.json` | counts only | yes, share this |
| `findings.jsonl` | every region with its hidden text | **no**: may hold live payloads |
| `most_common.jsonl` | most repeated hidden texts | **no** |

⚠️ Never paste `hidden_text` into an agent or run anything it says.
