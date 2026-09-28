# Portfolio samples

Three self-contained, runnable samples. **99 tests, 0 failures, zero
third-party dependencies.**

```bash
python run_all_tests.py     # one command, one green summary
```

---

## What is here

| # | Sample | What it demonstrates | Upwork / Fiverr keywords it targets |
|---|---|---|---|
| 01 | [Layered Memory Agent](01-ai-agent-memory/) | Agent architecture — three memory layers with different lifetimes, scored retrieval, consolidation, per-turn decision traces | `AI Agent` · `LLM` · `memory` · `architecture` |
| 02 | [Cleaning Pipeline](02-workflow-automation/) | Data pipeline that refuses to guess — quarantines bad rows with reasons, three exit codes, auditable report | `automation` · `data pipeline` · `ETL` · `n8n` · `Python` |
| 03 | [Parameter Extractor](03-doc-extraction/) | Structured extraction from vendor tables — radix correctness, cross-source collision detection, unparsed-line reporting | `data extraction` · `industrial` · `PLC` · `reverse engineering` |

| # | Code lines | Tests | Runtime deps |
|---|---|---|---|
| 01 | 680 | 21 | none |
| 02 | 699 | 39 | none |
| 03 | 657 | 39 | none |
| **Total** | **2036** | **99** | **none** |

Python 3.10+ and the standard library. No `pip install` step — that is
deliberate. A reviewer who can clone and run in twenty seconds actually runs it.

---

## The thread running through all three

Every sample is built around the same principle, which is the thing worth
selling:

> **Do not silently guess. Report what you could not do.**

- 01 — a memory older than its half-life loses to a fresh one; the decay is
  tested in both directions because it is easy to compute a recency term and
  have it never matter.
- 02 — `05/03/2026` is rejected rather than assigned a month; `1.234,50` is
  rejected rather than read as `1.23450`. Rejecting is cheaper than being wrong
  by 1000×.
- 03 — lines that fail to parse go to `unparsed.txt`, and two sources that
  disagree about a parameter's valid range produce a collision report instead of
  letting load order decide.

Each README has a **Known limitations** section listing what the sample does
*not* do. That section is not an apology — it is the most persuasive part of the
document. It proves the author tested the edges.

---

## How to use these

### 1. Publish them

Each sample should become its own public GitHub repo:

```bash
cd 01-ai-agent-memory
git init && git add . && git commit -m "Layered memory agent: three-layer architecture with scored retrieval"
# create the empty repo on GitHub, then:
git remote add origin https://github.com/<you>/layered-memory-agent.git
git push -u origin main
```

Do the same for the other two. Give them descriptive repo names — `layered-memory-agent`,
`csv-cleaning-pipeline`, `parameter-table-extractor` — not `portfolio-1`.

### 2. Fill in the licence

Each folder has a `LICENSE` with `[YOUR NAME]` in it. **Replace that before
pushing** — MIT with a placeholder owner looks careless.

### 3. Pin them on your profile

Pin the two most relevant repos on your Upwork profile. GitHub links in the
portfolio section carry real weight for technical clients because they can read
the commit history.

### 4. Use the right one in each proposal

When you bid, link the **one** sample that matches the client's actual problem.
Not all three. A client with a messy CSV wants sample 02, not your agent
architecture.

### 5. Quote your own limitations section

In the proposal, write something like:

> I have a small open-source sample that does exactly this — [link]. Its README
> lists what it does not handle (whole-file loading, no `.xlsx` support, unit
> comparison is textual). If any of those matter for your data, tell me and I
> will say so before we start rather than after.

This is the single highest-converting move available to a new freelancer.
It is also true, which is why it works.

### 6. Record a short screen capture

60–90 seconds: run `python run_all_tests.py` (green summary), then run one demo.
Clients rarely read code; they do watch a video of it working.

---

## What these are not

- **They are not client work.** They are purpose-built samples with synthetic
  data. Do not describe them as paid engagements — a client who asks a follow-up
  question will find out, and your rating is your only asset on the platform.
- **They are not production-hardened.** Each README says where the edges are.
- **`samples/mr_j4a.txt` is synthetic.** The parameter names are realistic but
  the values are invented for demonstration. Do not present it as a real vendor
  table.

---

## Before you publish — checklist

- [ ] Replace `[YOUR NAME]` in each `LICENSE`
- [ ] `git init` + first commit in each of the three folders
- [ ] Push to GitHub as three public repos with descriptive names
- [ ] Add a screenshot to each README (terminal output is fine)
- [ ] Pin two repos on your Upwork profile
- [ ] Confirm `python run_all_tests.py` prints 99 passed on a clean clone

---

## Files

| File | Purpose |
|---|---|
| `run_all_tests.py` | runs all three suites, exits non-zero on any failure |
| `01-ai-agent-memory/` | layered memory agent |
| `02-workflow-automation/` | CSV cleaning and validation pipeline |
| `03-doc-extraction/` | vendor parameter table extractor |
