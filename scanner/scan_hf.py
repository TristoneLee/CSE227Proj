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
# Structured metadata that is data, not prose.
_YAML_SKIP_PREFIXES = ("model-index", "co2_eq_emissions", "license_link", "thumbnail")
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
            if not path.startswith(_YAML_SKIP_PREFIXES):
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


def run(inp: str, out_dir: Path, with_yaml: bool, common: int, templates: set[str]) -> dict:
    findings = []
    n_cards = 0
    for rec in read_jsonl(inp):
        n_cards += 1
        for r in scan_card(rec["card"], with_yaml):
            r.setdefault("key", None)  # YAML key path for yaml_frontmatter
            findings.append({"id": rec["id"], "downloads": rec.get("downloads"),
                             "base_model": rec.get("base_model"), **r,
                             "text_key": text_key(r["hidden_text"])})

    cards_per_key: dict[str, set] = defaultdict(set)
    for f in findings:
        cards_per_key[f["text_key"]].add(f["id"])
    for f in findings:
        f["n_cards"] = len(cards_per_key[f["text_key"]])
        f["template"] = f["text_key"] in templates
        f["common"] = f["n_cards"] >= common
    rare = [f for f in findings if not (f["common"] or f["template"])]

    def by_tech(rows):
        cards: dict[str, set] = defaultdict(set)
        for f in rows:
            cards[f["technique"]].add(f["id"])
        return {t: {"regions": sum(1 for f in rows if f["technique"] == t), "cards": len(ids)}
                for t, ids in sorted(cards.items())}

    top = Counter()
    for f in findings:
        top[f["text_key"]] += 1
    example = {f["text_key"]: f["hidden_text"] for f in findings}
    summary = {
        "input": inp,
        "cards_scanned": n_cards,
        "cards_with_hidden_text": len({f["id"] for f in findings}),
        "cards_with_rare_hidden_text": len({f["id"] for f in rare}),
        "common_threshold_cards": common,
        "all_regions": by_tech(findings),
        "rare_regions": by_tech(rare),
    }
    # Most repeated hidden texts: candidates for scanner/hf_templates.txt.
    most_common = [{"text_key": k, "n_cards": len(cards_per_key[k]), "regions": c,
                    "preview": example[k][:160]} for k, c in top.most_common(40)]
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "findings.jsonl", "w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False, default=str) + "\n")
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
    args = ap.parse_args(argv)
    s = run(args.input, Path(args.out), args.yaml, args.common, load_templates(Path(args.templates)))
    print(f"scanned {s['cards_scanned']} cards: {s['cards_with_hidden_text']} with hidden text, "
          f"{s['cards_with_rare_hidden_text']} after dropping common/template text")
    for t, v in s["rare_regions"].items():
        print(f"  {t:20s} {v['cards']:>7} cards  {v['regions']:>7} regions")
    print(f"details: {args.out}/summary.json ; review most_common.jsonl to grow hf_templates.txt")


if __name__ == "__main__":
    main()
