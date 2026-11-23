"""LLM classification of the hidden text found in Hugging Face model cards.

  python classify/classify_hf.py results/hf_full --dry-run   # counts and cost estimate, no API calls
  python classify/classify_hf.py results/hf_full             # needs ANTHROPIC_API_KEY

Each distinct hidden text (by text_key) is classified once, however many cards contain
it. Only texts of at least 25 characters are sent, as in Radware's README run. We use
Radware's README prompt and parser unchanged (`ipiscan.prompts`), 8 snippets per
request, so our labels are comparable with theirs. The prompt says "GitHub README";
we leave that as is on purpose.

The model gets no tools. A payload can at worst skew its own label, which is why every
positive goes to hand review (review/) and why we report agreement with hand labels.

Writes, in the scan directory:
  classified.jsonl          one verdict per distinct text (git-ignored: reasons can quote payloads)
  classify_summary.json     counts only, safe to commit
Interrupted runs resume: texts already in classified.jsonl are skipped; API errors are retried.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from ipiscan import prompts
except ImportError:  # pragma: no cover
    sys.exit("ipiscan not installed: pip install -r requirements.txt")

MODEL = "claude-haiku-5-5"
PRICE_PER_MTOK = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00),
                  "claude-opus-5-5": (4.00, 20.00)}  # input, output; checked 2026-11-22
MAX_TOKENS = 4000  # room for brief thinking plus 8 verdicts


def load_distinct(scan_dir: Path, min_chars: int) -> list[dict]:
    """One entry per distinct hidden text, with how widely it appears."""
    texts: dict[str, dict] = {}
    with open(scan_dir / "findings.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            e = texts.get(d["text_key"])
            if e is None:
                e = texts[d["text_key"]] = {
                    "text_key": d["text_key"], "technique": d["technique"], "yaml_key": d.get("key"),
                    "hidden_text": d["hidden_text"], "n_cards": d["n_cards"], "common": d["common"],
                    "downloads": 0, "example_ids": []}
            e["downloads"] += d.get("downloads") or 0
            if len(e["example_ids"]) < 5 and d["id"] not in e["example_ids"]:
                e["example_ids"].append(d["id"])
    keep = [e for e in texts.values() if len(e["hidden_text"].strip()) >= min_chars]
    return sorted(keep, key=lambda e: e["text_key"])


def load_done(path: Path) -> dict[str, dict]:
    done = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if "api_error" not in row:
                done[row["text_key"]] = row
    return done


def classify_group(client, model: str, group: list[dict]) -> list[dict]:
    """Classify up to 8 snippets in one request. Returns one row per snippet."""
    import anthropic

    user = prompts.readme_user([e["hidden_text"] for e in group])
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        parsed = None
        for _ in range(2):  # one retry when the reply is not a complete array
            resp = client.messages.create(
                model=model, max_tokens=MAX_TOKENS, output_config={"effort": "low"},
                system=prompts.README_SYSTEM, messages=[{"role": "user", "content": user}])
            usage["input_tokens"] += resp.usage.input_tokens
            usage["output_tokens"] += resp.usage.output_tokens
            if resp.stop_reason == "refusal":
                err = "refusal"
                break
            reply = "".join(b.text for b in resp.content if b.type == "text")
            parsed = prompts.parse_readme(reply, len(group))
            if parsed:
                break
            err = f"incomplete JSON (stop_reason={resp.stop_reason})"
        if not parsed:
            return [{"text_key": e["text_key"], "api_error": err, **usage} for e in group]
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as ex:
        return [{"text_key": e["text_key"], "api_error": f"{type(ex).__name__}: {ex}"[:300]} for e in group]
    share = {k: v / len(group) for k, v in usage.items()}
    return [{"text_key": e["text_key"], "model": model,
             **{k: v.get(k) for k in ("is_injection", "explicit_prompt", "intent", "confidence", "reason")},
             **share} for e, v in zip(group, parsed)]


def summarize(distinct: list[dict], done: dict[str, dict], model: str) -> dict:
    by_key = {e["text_key"]: e for e in distinct}
    rows = [r for k, r in done.items() if k in by_key]
    pos = [r for r in rows if r.get("is_injection")]
    pin, pout = PRICE_PER_MTOK.get(model, (0, 0))
    tin = sum(r.get("input_tokens", 0) for r in rows)
    tout = sum(r.get("output_tokens", 0) for r in rows)

    def cards(rs):
        return sum(by_key[r["text_key"]]["n_cards"] for r in rs)

    return {
        "model": model,
        "distinct_texts_to_classify": len(distinct),
        "classified": len(rows),
        "positive_texts": len(pos),
        "positive_cards": cards(pos),
        "explicit_prompt_texts": sum(1 for r in pos if r.get("explicit_prompt")),
        "positives_by_intent": dict(Counter(str(r.get("intent")) for r in pos)),
        "positives_by_technique": dict(Counter(by_key[r["text_key"]]["technique"] for r in pos)),
        "positives_in_common_text": sum(1 for r in pos if by_key[r["text_key"]]["common"]),
        "tokens": {"input": round(tin), "output": round(tout)},
        "cost_usd": round(tin / 1e6 * pin + tout / 1e6 * pout, 4),
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan_dir", help="output directory of scanner/scan_hf.py, e.g. results/hf_full")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only the first N texts (for a trial run)")
    ap.add_argument("--dry-run", action="store_true", help="estimate size and cost, call nothing")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_dir)
    distinct = load_distinct(scan_dir, prompts.README_MIN_CHARS)
    if args.limit:
        distinct = distinct[: args.limit]
    out = scan_dir / "classified.jsonl"
    done = load_done(out)
    todo = [e for e in distinct if e["text_key"] not in done]
    groups = [todo[i:i + prompts.README_GROUP] for i in range(0, len(todo), prompts.README_GROUP)]

    if args.dry_run:
        sys_tok = len(prompts.README_SYSTEM) / 4
        snip_tok = sum(min(len(e["hidden_text"]), 1200) for e in todo) / 4
        tin, tout = len(groups) * sys_tok + snip_tok, len(groups) * 600
        pin, pout = PRICE_PER_MTOK.get(args.model, (0, 0))
        print(f"{len(distinct)} distinct texts >= {prompts.README_MIN_CHARS} chars, {len(done)} done, "
              f"{len(todo)} to do in {len(groups)} requests")
        print(f"estimate: ~{tin / 1e6:.2f}M input + ~{tout / 1e6:.2f}M output tokens "
              f"= ~${tin / 1e6 * pin + tout / 1e6 * pout:.2f} on {args.model} (rough: 4 chars/token)")
        return

    import anthropic

    client = anthropic.Anthropic(max_retries=5)
    with open(out, "a", encoding="utf-8") as fh, cf.ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(classify_group, client, args.model, g) for g in groups]
        for n, fut in enumerate(cf.as_completed(futures), 1):
            for row in fut.result():
                if "api_error" not in row:
                    done[row["text_key"]] = row
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            if n % 50 == 0 or n == len(groups):
                print(f"  {n}/{len(groups)} requests", flush=True)

    s = summarize(distinct, done, args.model)
    (scan_dir / "classify_summary.json").write_text(json.dumps(s, indent=1, ensure_ascii=False))
    print(json.dumps(s, indent=1, ensure_ascii=False))
    failed = len(distinct) - s["classified"]
    if failed:
        print(f"{failed} texts not classified yet (API errors); run again to retry them")


if __name__ == "__main__":
    main()
