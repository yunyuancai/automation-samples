"""Demo: a multi-turn session showing all three memory layers working.

    python demo.py

Runs with no API key (offline planner) so it always produces output.
Set DEEPSEEK_API_KEY to route turns through a real model instead.
"""

from __future__ import annotations

import json

from memory_agent import MemoryAgent


def rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> None:
    agent = MemoryAgent()
    mode = "LLM" if agent.llm.available else "offline planner (no API key set)"
    print(f"MemoryAgent demo — route: {mode}")

    # ---------------------------------------------------------------- 1
    rule("1. Writing memory: near-duplicates are merged, not duplicated")

    print(agent.remember("The client is migrating from Spreadsheet X to System Y"))
    print(agent.remember("The client's deadline is the end of March", importance=0.9))
    print(agent.remember("client is migrating from Spreadsheet X to System Y"))  # near-dup
    print(f"\nlong-term entries: {len(agent.longterm)}  (still 2 — the third reinforced)")

    # ---------------------------------------------------------------- 2
    rule("2. Session events decay; repetition gets consolidated")

    for _ in range(3):
        agent.note("the client asked for weekly progress reports")
    remark = agent.note("passing remark about the weather")

    print(f"episodic before consolidation: {len(agent.episodic)}")
    promoted = agent.consolidate(min_repeats=2)
    print(f"promoted to long-term: {promoted}")

    # Age the throwaway remark past its half-life and let pruning drop it.
    # This is what stops episodic memory from growing without bound.
    remark.created -= 10 * agent.episodic.half_life
    dropped = agent.episodic.prune()
    print(
        f"episodic after pruning: {len(agent.episodic)}"
        f"  (dropped {dropped} decayed event; the repeated one survived)"
    )
    print("\n-> the repeated request became a durable fact; the remark decayed away")

    # ---------------------------------------------------------------- 3
    rule("3. Retrieval: only the relevant memories reach the context")

    ctx = agent.build_context("when is the deadline?", k=3)
    print(ctx)
    print("\n-> scored on keyword overlap + recency + importance, not dumped wholesale")

    # ---------------------------------------------------------------- 4
    rule("4. A turn with tool routing")

    for line in ["what is 1250 * 0.19", "how many words are in this question"]:
        print(f"\nuser: {line}")
        print(f"agent: {agent.respond(line)}")

    # ---------------------------------------------------------------- 5
    rule("5. Decision trace (auditable, one entry per turn)")

    for entry in agent.trace:
        print(json.dumps(entry, ensure_ascii=False, indent=2)[:600])
        print("-" * 68)

    # ---------------------------------------------------------------- 6
    rule("6. Stats")
    print(json.dumps(agent.stats(), indent=2))

    rule("Why this architecture")
    print(
        "A single context window is not memory. It is bounded, it is\n"
        "expensive, and it forgets in the order things were said rather\n"
        "than in the order they matter.\n\n"
        "Splitting working / episodic / long-term lets each layer have its\n"
        "own lifetime and its own value: turns are cheap and volatile, events\n"
        "decay unless repeated, and only repeated or explicitly important\n"
        "things become durable. Retrieval then spends the context budget on\n"
        "the few memories that matter for THIS turn."
    )


if __name__ == "__main__":
    main()
