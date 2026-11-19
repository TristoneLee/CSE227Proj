# Registry Census

CSE 227 course research project, Fall 2026.

**Question:** How much text in npm, PyPI and Hugging Face model cards is hidden from humans but
aimed at AI agents, and what is it trying to do?

The full plan is in [`docs/project-details.pdf`](docs/project-details.pdf). Drop ideas into [`ideas/`](ideas/).

## What we would claim
1. The first census of this in package registries and model hubs.
2. Which hiding tricks (HTML comments, invisible Unicode, CSS) survive each registry's page
   renderer, so a human sees nothing but an agent reading the raw file does.

## Layout
| Folder | Purpose |
|---|---|
| `docs/` | Project plan, reading notes, methodology |
| `ideas/` | Free-form notes and proposals. Anyone can add a file |
| `data/{npm,pypi,huggingface}/` | Per-corpus pull scripts and notes. **Raw data is git-ignored** |
| `scanner/` | Hidden-text detector (port of Radware's scanner) |
| `classify/` | LLM step: is it aimed at an AI? Attack, promotion, defensive text, or the project's own agent notes? |
| `review/` | Hand-review guidelines and labels |
| `renderer-test/` | Local test of which hiding tricks survive each registry's renderer (nothing uploaded) |
| `results/` | Numbers and figures |
| `report/` | Final report (6 pages max) |
| `slides/` | Talk |

## Dates
- **By Sun Nov 22:** data pulled, scanner running on a sample of each corpus
- **Mon–Wed Nov 23–25:** full scans and hand review
- **Fri–Sun Nov 27–29:** numbers, figures, slides
- **Dec 1 or 3:** talk · **Dec 8, noon:** report due

## Split
One corpus each. Whoever finishes first takes the renderer test.

| Corpus | Owner |
|---|---|
| npm | _TBD_ |
| PyPI | _TBD_ |
| Hugging Face | _TBD_ |

## ⚠️ Safety
Payloads we find can be live attacks. **Do not paste them into an agent or run anything from
them.** Keep raw dumps and flagged payloads out of git.
