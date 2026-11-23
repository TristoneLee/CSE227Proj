# Classify

LLM step: is each hidden text aimed at an AI? If so: malicious / self_serving / anti_ai / operational.

```bash
python classify/classify_hf.py results/hf_full --dry-run   # size and cost, no API calls
python classify/classify_hf.py results/hf_full --limit 80  # trial run (10 requests)
python classify/classify_hf.py results/hf_full             # full run, resumable
```

- Needs `ANTHROPIC_API_KEY` in the environment. Never commit it or paste it in chat.
- Uses Radware's README prompt and parser unchanged (Claude Haiku 5.5, 8 snippets per request, texts ≥ 25 chars) so labels are comparable with their GitHub numbers.
- Each distinct text is classified once. Full Hugging Face run: 6,344 texts, 793 requests, about $0.40.
- The model gets no tools. Every positive still goes to hand review (`review/`).
- `classified.jsonl` is git-ignored (reasons can quote payloads); `classify_summary.json` is counts only.
