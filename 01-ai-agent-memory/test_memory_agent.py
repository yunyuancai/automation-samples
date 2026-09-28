"""Tests for the layered memory agent.

No pytest required — run directly:

    python test_memory_agent.py

(pytest also works if you have it: `pytest test_memory_agent.py`.)
"""

from __future__ import annotations

import time

from memory_agent import (
    EpisodicMemory,
    LLM,
    LongTermMemory,
    Memory,
    MemoryAgent,
    Retriever,
    WorkingMemory,
    _tool_calculator,
    _tool_word_count,
    default_tools,
    overlap,
    tokenize,
)


# -- tokenisation ----------------------------------------------------------


def test_tokenize_handles_english_and_cjk():
    assert tokenize("Hello World") == {"hello", "world"}
    # CJK is split per character — crude but dependency-free and good enough
    # for overlap scoring without a segmenter.
    assert "记" in tokenize("记忆架构")


def test_overlap_bounds():
    assert overlap(set(), {"a"}) == 0.0
    assert overlap({"a", "b"}, {"a", "b"}) == 1.0
    assert 0.0 < overlap({"a", "b"}, {"b", "c"}) < 1.0


# -- working memory --------------------------------------------------------


def test_working_memory_is_bounded():
    wm = WorkingMemory(capacity=3)
    for i in range(10):
        wm.add("user", f"turn {i}")
    assert len(wm) == 3
    rendered = wm.render()
    assert "turn 9" in rendered
    assert "turn 0" not in rendered  # oldest evicted


# -- episodic memory -------------------------------------------------------


def test_episodic_prune_removes_decayed_events():
    em = EpisodicMemory(half_life=10.0)
    fresh = em.add("important thing", importance=0.9)
    stale = em.add("trivial thing", importance=0.5)

    # Pretend a long time passed for the stale item only.
    stale.created = time.time() - 10_000

    dropped = em.prune(floor=0.05)
    assert dropped == 1
    remaining = [m.text for m in em.all()]
    assert fresh.text in remaining
    assert stale.text not in remaining


def test_recency_decays_toward_zero():
    m = Memory(text="x")
    now = m.created
    assert m.recency(now, half_life=100) == 1.0
    assert abs(m.recency(now + 100, half_life=100) - 0.5) < 1e-9


# -- long-term memory ------------------------------------------------------


def test_near_duplicate_detection():
    lt = LongTermMemory()
    lt.add("The user prefers dark mode")
    hit = lt.find_near_duplicate("user prefers dark mode")
    assert hit is not None

    miss = lt.find_near_duplicate("completely unrelated sentence about servers")
    assert miss is None


# -- retrieval -------------------------------------------------------------


def test_retriever_prefers_keyword_relevance():
    """Keyword relevance must outweigh a *moderate* recency disadvantage.

    Note the recency gap is deliberately modest (half a half-life). A memory
    that is 100 half-lives old is supposed to lose — see the test below.
    """
    r = Retriever(kw=0.5, rc=0.25, im=0.25)
    now = time.time()

    relevant = Memory(text="the deployment script lives in tools/deploy.py",
                      importance=0.5, created=now - 5_000)
    recent_but_irrelevant = Memory(text="unrelated chatter about lunch",
                                   importance=0.5, created=now)

    ranked = r.rank(
        "where is the deployment script",
        [(relevant, 10_000.0), (recent_but_irrelevant, 10_000.0)],
        k=2,
        now=now,
    )
    assert ranked[0].memory is relevant
    assert ranked[0].parts["keyword"] > 0
    assert ranked[1].parts["keyword"] == 0


def test_fully_decayed_memory_loses_to_fresh_one():
    """The complementary behaviour: decay is real, not decorative.

    A relevant memory older than ~100 half-lives carries essentially no
    recency signal and should not outrank a fresh one on recency alone.
    """
    r = Retriever(kw=0.5, rc=0.25, im=0.25)
    now = time.time()

    ancient = Memory(text="the deployment script lives in tools/deploy.py",
                     importance=0.5, created=now - 100_000)
    fresh = Memory(text="unrelated chatter about lunch",
                   importance=0.5, created=now)

    ranked = r.rank(
        "where is the deployment script",
        [(ancient, 1000.0), (fresh, 1000.0)],
        k=2,
        now=now,
    )
    assert ranked[0].memory is fresh
    assert ranked[0].parts["recency"] == 1.0
    assert ranked[1].parts["recency"] < 1e-6


def test_retriever_touches_returned_memories_only():
    r = Retriever()
    now = time.time()
    a = Memory(text="alpha beta", importance=0.5)
    b = Memory(text="gamma delta", importance=0.5)
    r.rank("alpha", [(a, 1000.0), (b, 1000.0)], k=1, now=now)
    assert a.access_count == 1
    assert b.access_count == 0


def test_weights_are_normalised():
    r = Retriever(kw=2, rc=1, im=1)
    assert abs((r.kw + r.rc + r.im) - 1.0) < 1e-9


# -- tools -----------------------------------------------------------------


def test_calculator_evaluates_arithmetic():
    assert _tool_calculator({"expression": "2+3*4"}) == "14"


def test_calculator_rejects_non_arithmetic():
    # A whitelist is enforced before eval, so injected code never reaches it.
    out = _tool_calculator({"expression": "__import__('os').system('ls')"})
    assert out.startswith("error:")


def test_word_count():
    assert _tool_word_count({"text": "one two three"}) == "3"


def test_default_tools_registered():
    names = default_tools().names()
    assert "calculator" in names and "word_count" in names


# -- agent behaviour -------------------------------------------------------


def _offline_agent() -> MemoryAgent:
    """Force the offline planner so tests never depend on network or keys."""
    llm = LLM()
    llm.key = None
    return MemoryAgent(llm=llm)


def test_remember_dedupes_and_reinforces():
    agent = _offline_agent()
    first = agent.remember("The user is based in Berlin")
    assert first.startswith("stored:")
    assert len(agent.longterm) == 1

    second = agent.remember("user is based in Berlin")
    assert second.startswith("reinforced:")
    assert len(agent.longterm) == 1                      # no duplicate added
    assert agent.longterm.all()[0].importance > 0.7      # reinforced


def test_consolidation_promotes_repeated_events():
    agent = _offline_agent()
    for _ in range(3):
        agent.note("the client wants weekly reports")
    agent.note("one-off remark")

    promoted = agent.consolidate(min_repeats=2)
    assert any("weekly reports" in p for p in promoted)
    assert all("one-off" not in p for p in promoted)
    assert len(agent.longterm) == 1


def test_respond_offline_routes_to_calculator():
    agent = _offline_agent()
    answer = agent.respond("what is 12 * 4")
    assert "48" in answer
    assert agent.trace[-1]["tool"] == "calculator"
    assert agent.trace[-1]["route"] == "offline"


def test_respond_records_trace_and_working_memory():
    agent = _offline_agent()
    agent.respond("hello there")
    assert len(agent.trace) == 1
    assert agent.trace[0]["input"] == "hello there"
    assert len(agent.working) == 2          # user turn + assistant turn


def test_build_context_empty_when_no_memory():
    agent = _offline_agent()
    assert agent.build_context("anything") == "(no relevant memory)"


def test_context_includes_relevant_fact():
    agent = _offline_agent()
    agent.remember("The staging server is at 10.0.0.5")
    agent.remember("The client's name is Dana")
    ctx = agent.build_context("where is the staging server")
    assert "10.0.0.5" in ctx


def test_stats_shape():
    agent = _offline_agent()
    agent.remember("something")
    s = agent.stats()
    assert s["longterm"] == 1
    assert s["llm"] is False
    assert isinstance(s["tools"], list)


# -- runner ----------------------------------------------------------------


def _run_all() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed.append((name, f"AssertionError: {exc}"))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
        else:
            passed += 1
            print(f"  PASS  {name}")

    print()
    for name, err in failed:
        print(f"  FAIL  {name}\n        {err}")
    print(f"\n{passed} passed, {len(failed)} failed, {len(tests)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
