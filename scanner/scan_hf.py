"""Scan Hugging Face model cards for text hidden from humans.

  python scanner/scan_hf.py data/huggingface/cards.sample10000.jsonl.gz --out results/hf_sample10000
  python scanner/scan_hf.py ... --yaml       # also report long free text in the YAML header (experimental)

Input is the export from data/huggingface/download.py. Writes:
  <out>/findings.jsonl     one line per hidden region          (git-ignored: may hold LIVE PAYLOADS)
  <out>/most_common.jsonl  the most repeated hidden texts      (git-ignored: same reason)
  <out>/summary.json       counts only, no hidden text: safe to commit and share

Detection is Radware's deterministic `ipiscan.detect` with the README techniques
(HTML comments, white LaTeX, CSS hiding, invisible Unicode), plus its noise filter.
We add two things for model cards:
  * The YAML header (--- ... ---) is split off before scanning. Hugging Face shows it
    as tags, not as text. With --yaml, long string values in it are reported as
    technique `yaml_frontmatter`.
  * Duplicate counting. Each region gets `n_cards`, the number of distinct cards that
    contain the same normalized text. Hugging Face's card template is full of HTML
    comments, and fine-tunes copy their base model's card. A region in >= --common
    cards is marked `common`, and the summary reports counts with and without it.
    Known template strings can be listed in scanner/hf_templates.txt.

SAFETY: never paste `hidden_text` into an agent or run anything it says.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from ipiscan.detect import README_TECHNIQUES, scan
    from ipiscan.filters import is_noise_region
except ImportError:  # pragma: no cover
    sys.exit("ipiscan not installed: pip install -r requirements.txt")

HERE = Path(__file__).resolve().parent
_FRONT_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL)
# Documented model-card metadata keys. Hugging Face shows these on the page (as tags, the
# inference widget, the gating form, ...) or they are structured data, so text in them is
# not hidden. Any other top-level key is a custom key that the page probably does not show
# at all. That is an assumption: the renderer test has to confirm it.
HF_DOCUMENTED_KEYS = frozenset({
    "language", "license", "license_name", "license_link", "library_name", "tags", "datasets",
    "metrics", "base_model", "base_model_relation", "pipeline_tag", "widget", "inference",
    "model-index", "co2_eq_emissions", "thumbnail", "new_version", "model_name",
    "extra_gated_prompt", "extra_gated_fields", "extra_gated_heading",
    "extra_gated_description", "extra_gated_button_content",
})
YAML_MIN_CHARS = 40


def split_front_matter(card: str) -> tuple[str, str, int]:
    """Return (yaml_text, body, body_offset)."""
    m = _FRONT_RE.match(card)
    if not m:
        return "", card, 0
    return m.group(1), card[m.end():], m.end()


def yaml_strings(yaml_text: str) -> list[dict]:
    import yaml

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return []
    out: list[dict] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and len(node.strip()) >= YAML_MIN_CHARS:
            top = re.split(r"[.\[]", path, maxsplit=1)[0]
            if top not in HF_DOCUMENTED_KEYS:
                out.append({"technique": "yaml_frontmatter", "hidden_text": node.strip()[:4000],
                            "offset": 0, "key": path})

    walk(data, "")
    return out


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def text_key(text: str) -> str:
    return hashlib.sha1(normalize(text).encode()).hexdigest()[:16]


def load_templates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return {text_key(ln) for ln in lines if ln and not ln.startswith("#")}


def scan_card(card: str, with_yaml: bool = False) -> list[dict]:
    yaml_text, body, off = split_front_matter(card)
    regions = []
    for r in scan(body, README_TECHNIQUES):
        if is_noise_region(r.get("hidden_text", "")):
            continue
        regions.append({**r, "offset": r["offset"] + off})
    if with_yaml and yaml_text:
        regions = yaml_strings(yaml_text) + regions
    return regions


def read_jsonl(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _scan_batch(args: tuple[list[dict], bool]) -> tuple[int, list[dict]]:
    """Scan a batch of cards. Returns (number of cards, findings)."""
    recs, with_yaml = args
    out = []
    for rec in recs:
        for r in scan_card(rec["card"], with_yaml):
            r.setdefault("key", None)  # YAML key path for yaml_frontmatter
            out.append({"id": rec["id"], "downloads": rec.get("downloads"),
                        "base_model": rec.get("base_model"), **r,
                        "text_key": text_key(r["hidden_text"])})
    return len(recs), out


def _batches(inp: str, size: int, with_yaml: bool):
    batch = []
    for rec in read_jsonl(inp):
        batch.append(rec)
        if len(batch) >= size:
            yield batch, with_yaml
            batch = []
    if batch:
        yield batch, with_yaml


def run(inp: str, out_dir: Path, with_yaml: bool, common: int, templates: set[str],
        workers: int = 1, batch: int = 500) -> dict:
    """Two passes so memory stays flat: pass 1 scans (in parallel) and streams raw
    findings to disk while counting, per text, how many distinct cards contain it.
    Pass 2 adds n_cards / common / template to each finding and builds the summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "findings.tmp.jsonl"
    n_cards = 0
    cards_per_key: Counter = Counter()
    regions_per_key: Counter = Counter()
    example: dict[str, str] = {}

    def consume(results: list[dict], fh) -> None:
        last_id, keys_in_card = None, set()
        for f in results:
            if f["id"] != last_id:
                cards_per_key.update(keys_in_card)
                last_id, keys_in_card = f["id"], set()
            keys_in_card.add(f["text_key"])
            regions_per_key[f["text_key"]] += 1
            example.setdefault(f["text_key"], f["hidden_text"][:160])
            fh.write(json.dumps(f, ensure_ascii=False, default=str) + "\n")
        cards_per_key.update(keys_in_card)

    with open(tmp, "w", encoding="utf-8") as fh:
        if workers > 1:
            import multiprocessing as mp

            with mp.Pool(workers) as pool:
                results = pool.imap(_scan_batch, _batches(inp, batch, with_yaml), chunksize=4)
                for n, res in results:
                    n_cards += n
                    consume(res, fh)
        else:
            for n, res in map(_scan_batch, _batches(inp, batch, with_yaml)):
                n_cards += n
                consume(res, fh)

    def is_rare(k: str) -> bool:
        return cards_per_key[k] < common and k not in templates

    all_cards: dict[str, set] = defaultdict(set)
    rare_cards: dict[str, set] = defaultdict(set)
    all_regions: Counter = Counter()
    rare_regions: Counter = Counter()
    with open(tmp, encoding="utf-8") as src, open(out_dir / "findings.jsonl", "w", encoding="utf-8") as dst:
        for line in src:
            f = json.loads(line)
            k, t = f["text_key"], f["technique"]
            f["n_cards"] = cards_per_key[k]
            f["template"] = k in templates
            f["common"] = f["n_cards"] >= common
            all_cards[t].add(f["id"])
            all_regions[t] += 1
            if is_rare(k):
                rare_cards[t].add(f["id"])
                rare_regions[t] += 1
            dst.write(json.dumps(f, ensure_ascii=False, default=str) + "\n")
    tmp.unlink()

    def by_tech(cards, regions):
        return {t: {"regions": regions[t], "cards": len(cards[t])} for t in sorted(cards)}

    summary = {
        "input": inp,
        "cards_scanned": n_cards,
        "cards_with_hidden_text": len(set().union(*all_cards.values())) if all_cards else 0,
        "cards_with_rare_hidden_text": len(set().union(*rare_cards.values())) if rare_cards else 0,
        "common_threshold_cards": common,
        "distinct_hidden_texts": len(cards_per_key),
        "all_regions": by_tech(all_cards, all_regions),
        "rare_regions": by_tech(rare_cards, rare_regions),
    }
    # Most repeated hidden texts: candidates for scanner/hf_templates.txt.
    most_common = [{"text_key": k, "n_cards": c, "regions": regions_per_key[k], "preview": example[k]}
                   for k, c in cards_per_key.most_common(200)]
    with open(out_dir / "most_common.jsonl", "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in most_common)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return {**summary, "most_common": most_common}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="jsonl(.gz) from data/huggingface/download.py export")
    ap.add_argument("--out", required=True, help="output directory, e.g. results/hf_sample10000")
    ap.add_argument("--yaml", action="store_true", help="also report long strings in the YAML header")
    ap.add_argument("--common", type=int, default=50, help="mark text found in >= N cards as common")
    ap.add_argument("--templates", default=str(HERE / "hf_templates.txt"))
    ap.add_argument("--workers", type=int, default=1, help="parallel processes")
    args = ap.parse_args(argv)
    s = run(args.input, Path(args.out), args.yaml, args.common, load_templates(Path(args.templates)),
            workers=args.workers)
    print(f"scanned {s['cards_scanned']} cards: {s['cards_with_hidden_text']} with hidden text, "
          f"{s['cards_with_rare_hidden_text']} after dropping common/template text")
    for t, v in s["rare_regions"].items():
        print(f"  {t:20s} {v['cards']:>7} cards  {v['regions']:>7} regions")
    print(f"details: {args.out}/summary.json ; review most_common.jsonl to grow hf_templates.txt")


if __name__ == "__main__":
    main()
