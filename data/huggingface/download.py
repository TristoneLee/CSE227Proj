"""Download the Hugging Face model-card dump and export it as one normalized file.

Source: the dataset `modelbiome/ai_ecosystem_withmodelcards` on the Hugging Face Hub,
pinned to revision 4cb5d87 (models snapshot 2025-07-13, cards scraped by 2025-07-21).
It is a single 5.2 GB CSV. The Hub's auto-converted parquet is only partial, so we
download the CSV. Columns: model_id, modelCard, downloads, parent_model, ...

Steps:
  python data/huggingface/download.py inspect                 # list shards, print schema + 2 rows
  python data/huggingface/download.py fetch [--max-shards N]  # parquet -> data/huggingface/raw/
  python data/huggingface/download.py export --sample 10000   # -> data/huggingface/cards.sample10000.jsonl.gz
  python data/huggingface/download.py export                  # all cards -> data/huggingface/cards.jsonl.gz

Each exported line is {"id", "card", "downloads", "base_model", "source"}.

The sample is a bottom-k hash sample. A card is kept when hash(seed, id) is among the
N smallest hashes, so it is uniform, reproducible, and independent of shard order.
It only covers the shards you fetched, so fetch every shard before taking the sample
you report.

Column names are detected automatically. If detection picks the wrong column, override
it with --card-col / --id-col / --downloads-col / --base-col after running `inspect`.
Raw data is git-ignored. Do not commit it.
"""
from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import heapq
import json
import sys
from pathlib import Path

REPO_ID = "modelbiome/ai_ecosystem_withmodelcards"
REVISION = "4cb5d8739a8fce7c03826994dd756c244b4126bf"
DATA_SUFFIXES = (".parquet", ".csv")
HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"

CANDIDATES = {
    "card": ["card", "modelcard", "model_card", "card_text", "modelcard_text", "readme", "text"],
    "id": ["id", "modelId", "model_id", "repo_id", "name"],
    "downloads": ["downloads", "downloads_all_time", "downloadsAllTime", "downloads_30d"],
    "base": ["base_model", "base_models", "parent_model", "base"],
}


def pick(columns: list[str], kind: str, override: str | None) -> str | None:
    if override:
        if override not in columns:
            sys.exit(f"--{kind}-col {override!r} not in columns: {columns}")
        return override
    lower = {c.lower(): c for c in columns}
    for name in CANDIDATES[kind]:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def data_files() -> list[str]:
    from huggingface_hub import list_repo_files

    files = sorted(f for f in list_repo_files(REPO_ID, repo_type="dataset", revision=REVISION)
                   if f.endswith(DATA_SUFFIXES))
    if not files:
        sys.exit(f"no data files found in {REPO_ID}; check the dataset page")
    return files


def fetch_files(files: list[str]) -> list[Path]:
    from huggingface_hub import hf_hub_download

    return [Path(hf_hub_download(REPO_ID, f, repo_type="dataset", revision=REVISION, local_dir=RAW))
            for f in files]


def _parse_list(v):
    """parent_model and friends are stored as Python-list strings, e.g. "['a/b']"."""
    if isinstance(v, str) and v.startswith("["):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    return v


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def iter_rows(path: Path, batch_size: int = 2000):
    """Yield (columns, list_of_row_dicts) batches from a parquet or CSV file."""
    if path.suffix == ".csv":
        csv.field_size_limit(sys.maxsize)
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append(row)
                if len(batch) >= batch_size:
                    yield reader.fieldnames, batch
                    batch = []
            if batch:
                yield reader.fieldnames, batch
    else:
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        for b in pf.iter_batches(batch_size=batch_size):
            yield pf.schema_arrow.names, b.to_pylist()


def cmd_inspect(args) -> None:
    files = data_files()
    print(f"{len(files)} data file(s) in {REPO_ID}@{REVISION[:7]}:")
    for f in files[:20]:
        print("  ", f)
    if len(files) > 20:
        print(f"   ... and {len(files) - 20} more")
    local = sorted(p for p in RAW.rglob("*") if p.suffix in DATA_SUFFIXES)
    if not local:
        sys.exit("\nnothing downloaded yet; run `fetch` (the CSV is 5.2 GB), then `inspect` again")
    cols, rows = next(iter_rows(local[0], batch_size=2))
    print(f"\ncolumns of {local[0].name}: {cols}")
    print("\nauto-detected:", {k: pick(cols, k, None) for k in CANDIDATES})
    for row in rows:
        print(json.dumps({k: (str(v)[:200] if v is not None else None) for k, v in row.items()},
                         ensure_ascii=False, indent=1))


def cmd_fetch(args) -> None:
    files = data_files()
    if args.max_shards:
        files = files[: args.max_shards]
    for p in fetch_files(files):
        print("ok", p.relative_to(HERE))


def _hash(seed: int, key: str) -> int:
    return int.from_bytes(hashlib.blake2b(f"{seed}:{key}".encode(), digest_size=8).digest(), "big")


def iter_cards(args):
    paths = sorted(p for p in RAW.rglob("*") if p.suffix in DATA_SUFFIXES)
    if not paths:
        sys.exit("no data in data/huggingface/raw/; run `fetch` first")
    for path in paths:
        c = None
        for cols, rows in iter_rows(path):
            if c is None:
                c = {k: pick(cols, k, getattr(args, f"{k}_col")) for k in CANDIDATES}
                if not c["card"] or not c["id"]:
                    sys.exit(f"{path.name}: cannot find card/id columns in {cols}; use --card-col/--id-col")
            for row in rows:
                card = row.get(c["card"])
                if not isinstance(card, str) or not card.strip() or card.strip() in ("nan", "None"):
                    continue
                yield {
                    "id": str(row[c["id"]]),
                    "card": card,
                    "downloads": _int(row.get(c["downloads"])) if c["downloads"] else None,
                    "base_model": _parse_list(row.get(c["base"])) if c["base"] else None,
                    "source": "huggingface",
                }


def cmd_export(args) -> None:
    out = Path(args.out) if args.out else HERE / (
        f"cards.sample{args.sample}.jsonl.gz" if args.sample else "cards.jsonl.gz")
    seen = 0
    if args.sample:
        heap: list[tuple[int, str, dict]] = []  # max-heap on hash via negation
        for rec in iter_cards(args):
            seen += 1
            h = -_hash(args.seed, rec["id"])
            if len(heap) < args.sample:
                heapq.heappush(heap, (h, rec["id"], rec))
            elif h > heap[0][0]:
                heapq.heapreplace(heap, (h, rec["id"], rec))
        rows = [r for _, _, r in sorted(heap, key=lambda t: t[1])]
    n = 0
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for rec in rows if args.sample else iter_cards(args):
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            n += 1
    print(f"read {seen if args.sample else n} non-empty cards, wrote {n} -> {out}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect").set_defaults(fn=cmd_inspect)
    f = sub.add_parser("fetch")
    f.add_argument("--max-shards", type=int, default=0, help="only the first N files (for a quick test)")
    f.set_defaults(fn=cmd_fetch)
    e = sub.add_parser("export")
    e.add_argument("--sample", type=int, default=0, help="keep N cards (0 = all)")
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--out")
    for k in CANDIDATES:
        e.add_argument(f"--{k}-col", dest=f"{k}_col")
    e.set_defaults(fn=cmd_export)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
