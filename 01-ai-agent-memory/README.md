# Layered Memory Agent

An AI agent with a real memory architecture — three layers with different
lifetimes, scored retrieval, and a consolidation step — instead of a prompt
wrapped around a chat API.

**Zero third-party dependencies.** Python 3.10+ and the standard library only.

```bash
python demo.py               # runnable session, no API key needed
python test_memory_agent.py  # 21 tests, no pytest required
```

---

## The problem

Most "AI agents with memory" are one of these:

- **Stuff everything in the context window.** Works until it doesn't: the
  window fills, cost scales with conversation length, and the model gets
  distracted by irrelevant history.
- **Dump the whole conversation into a vector store and retrieve top-k.** Better,
  but it treats a throwaway remark and a stated requirement as equally
  memorable, and it never forgets anything.

Neither has an answer to "what should this agent remember, and for how long."

## The architecture

```
                      ┌─────────────────────────────┐
   user turn ────────▶│  working   last N turns     │  seconds
                      │            verbatim, bounded│
                      └──────────────┬──────────────┘
                                     │ note()
                      ┌──────────────▼──────────────┐
                      │  episodic  timestamped      │  ~30 min half-life
                      │            events, decays   │
                      └──────────────┬──────────────┘
                                     │ consolidate(min_repeats=2)
                      ┌──────────────▼──────────────┐
                      │  longterm  consolidated facts│  ~30 day half-life
                      │            durable          │
                      └──────────────┬──────────────┘
                                     │
                      ┌──────────────▼──────────────┐
                      │  Retriever                  │
                      │  score = 0.5·keyword        │
                      │        + 0.25·recency       │
                      │        + 0.25·importance    │
                      └──────────────┬──────────────┘
                                     │ top-k only
                                     ▼
                              context window
```

Each layer has its own lifetime, and content **moves between layers** rather
than accumulating in one place:

| Layer | Lifetime | What lands here | Why |
|---|---|---|---|
| working | bounded (8 turns) | every turn, verbatim | the model needs the immediate thread |
| episodic | ~30 min half-life | events, observations | cheap, but should fade if nothing comes of it |
| longterm | ~30 day half-life | consolidated facts | durable, and worth the context budget |

### Three design decisions worth calling out

**1. Repetition is the promotion signal.** `consolidate()` promotes an episodic
event to long-term once it has been observed `min_repeats` times. Something the
user says three times is a requirement; something they say once is usually
noise. This is the step most implementations skip, and it is the one that
actually produces long-term behaviour.

**2. Near-duplicates are reinforced, not appended.** Writing the same fact twice
bumps its importance instead of storing it twice. Without this, the store fills
with paraphrases of the same statement and retrieval quality degrades.

**3. Decay is real, not decorative.** A memory older than ~100 half-lives
carries essentially no recency signal and loses to a fresh one. There is an
explicit test for both directions
(`test_retriever_prefers_keyword_relevance` and
`test_fully_decayed_memory_loses_to_fresh_one`) because it is easy to write
scoring code where the recency term is computed and then never matters.

## What you get per turn

Every turn records a **decision trace**: the input, the exact context that was
retrieved (with per-memory scores), which tool ran, and the route taken.

```json
{
  "input": "what is 1250 * 0.19",
  "context": "[event 0.71] user asked: what is 1250 * 0.19\n[fact 0.52] The client's deadline is the end of March\n...",
  "tool": "calculator",
  "route": "offline",
  "answer": "[offline planner] calculator -> 237.5"
}
```

This is not decoration. When an agent gives a wrong answer, the first question
is always "what did it actually have in front of it?" — a trace answers that
without re-running anything.

## Verification

```
$ python test_memory_agent.py
21 passed, 0 failed, 21 total
```

Covered: tokenisation (English + CJK), overlap bounds, working-memory eviction,
recency decay curve, episodic pruning by decayed weight, near-duplicate
detection, retrieval ranking in **both** directions, weight normalisation,
access-count bookkeeping, calculator input whitelisting, tool registry,
remember/reinforce, consolidation, offline tool routing, trace integrity, and
stats shape.

The calculator test is a security test: `__import__('os').system(...)` is
rejected because a character whitelist is enforced *before* `eval` is reached.

## Running against a real model

Set a key and turns route through an OpenAI-compatible endpoint instead of the
offline planner:

```bash
export DEEPSEEK_API_KEY=sk-...          # or OPENAI_API_KEY
export LLM_BASE_URL=https://api.deepseek.com/v1   # optional
export LLM_MODEL=deepseek-chat                    # optional
python demo.py
```

With no key the offline planner answers, so the demo always produces output and
the test suite never touches the network.

## Known limitations

Stated plainly, because you should know what you are and are not getting:

- **Retrieval is lexical, not semantic.** `tokenize()` is word-splitting plus
  per-character CJK. It has no notion of synonyms — "car" and "automobile" do
  not match. Swapping in embeddings means replacing `tokenize`/`overlap` and
  leaving the scoring shape alone; the seam is deliberately narrow.
- **No persistence.** Everything is in memory and dies with the process. Adding
  SQLite means writing `to_dict()`/`from_dict()` for each layer — the dataclasses
  already expose what is needed.
- **Half-lives are chosen, not learned.** 30 min / 30 days are reasonable
  defaults, not tuned values. They are constructor parameters precisely so they
  can be tuned per deployment.
- **`consolidate()` groups by exact token-set equality.** Two phrasings of the
  same requirement will not merge into one promotion. `find_near_duplicate`
  already does fuzzy matching and is the obvious upgrade path.
- **No concurrency control.** Single-threaded by design; a shared store across
  workers needs locking.

## Files

| File | Lines | Contents |
|---|---|---|
| `memory_agent.py` | 406 | the three layers, retriever, tools, LLM adapter, agent |
| `test_memory_agent.py` | 206 | 21 tests, plain-assert, runs without pytest |
| `demo.py` | 68 | six-part walkthrough with a live decision trace |

---

*Built as a portfolio sample. The architecture mirrors one used in production;
this is a self-contained extraction with no external dependencies so it can be
run and audited in under a minute.*
