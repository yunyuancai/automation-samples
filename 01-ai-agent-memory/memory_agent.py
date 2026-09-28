"""
Layered memory architecture for an AI agent.

Three layers, because a single context window is not memory:

  working   - the last N turns, verbatim. Cheap, volatile, bounded.
  episodic  - timestamped events for the current session. Searchable, decays.
  longterm  - consolidated facts that survived repetition. High value, durable.

Retrieval scores candidates across three signals (keyword overlap, recency,
importance) instead of dumping everything into the prompt. That is the whole
point: the context window is a scarce resource, so it should be spent on the
few memories that actually matter for the current turn.

Zero third-party dependencies. An LLM is optional: if DEEPSEEK_API_KEY (or
OPENAI_API_KEY) is set the agent talks to it, otherwise a deterministic
offline planner answers so the demo always runs.

Run the demo:   python demo.py
Run the tests:  python test_memory_agent.py
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable

# --------------------------------------------------------------------------
# tokenisation / similarity
# --------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens. CJK is split per character (no segmenter needed).

    Deliberately crude and dependency-free. A real deployment would swap this
    for embeddings; the scoring shape below stays the same.
    """
    return set(_WORD.findall(text.lower()))


def overlap(a: set[str], b: set[str]) -> float:
    """Jaccard-style overlap in [0, 1]."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------
# memory layers
# --------------------------------------------------------------------------


@dataclass
class Memory:
    """One retrievable unit."""

    text: str
    kind: str = "fact"                     # fact | event | preference
    importance: float = 0.5                # 0..1, set by the writer
    created: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    access_count: int = 0
    tags: tuple[str, ...] = ()

    def touch(self) -> None:
        self.access_count += 1
        self.last_access = time.time()

    def recency(self, now: float, half_life: float) -> float:
        """Exponential decay in [0, 1]."""
        age = max(0.0, now - self.created)
        return math.pow(0.5, age / half_life)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "kind": self.kind,
            "importance": round(self.importance, 3),
            "access_count": self.access_count,
            "tags": list(self.tags),
        }


class WorkingMemory:
    """The last N turns, verbatim. Bounded on purpose."""

    def __init__(self, capacity: int = 8) -> None:
        self.capacity = capacity
        self._turns: deque[tuple[str, str]] = deque(maxlen=capacity)

    def add(self, role: str, text: str) -> None:
        self._turns.append((role, text))

    def render(self) -> str:
        if not self._turns:
            return "(no prior turns)"
        return "\n".join(f"{role}: {text}" for role, text in self._turns)

    def __len__(self) -> int:
        return len(self._turns)


class EpisodicMemory:
    """Timestamped events for the current session. Decays, then gets dropped."""

    def __init__(self, half_life: float = 1800.0) -> None:
        self.half_life = half_life
        self._items: list[Memory] = []

    def add(self, text: str, importance: float = 0.4, tags: Iterable[str] = ()) -> Memory:
        m = Memory(text=text, kind="event", importance=importance, tags=tuple(tags))
        self._items.append(m)
        return m

    def prune(self, floor: float = 0.05, now: float | None = None) -> int:
        """Drop events whose decayed weight has fallen below `floor`."""
        now = now or time.time()
        before = len(self._items)
        self._items = [
            m for m in self._items if m.importance * m.recency(now, self.half_life) >= floor
        ]
        return before - len(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Memory]:
        return list(self._items)


class LongTermMemory:
    """Consolidated facts. Durable; only promoted content lands here."""

    def __init__(self, half_life: float = 30 * 24 * 3600.0) -> None:
        self.half_life = half_life
        self._items: list[Memory] = []

    def add(
        self,
        text: str,
        importance: float = 0.7,
        kind: str = "fact",
        tags: Iterable[str] = (),
    ) -> Memory:
        m = Memory(text=text, kind=kind, importance=importance, tags=tuple(tags))
        self._items.append(m)
        return m

    def find_near_duplicate(self, text: str, threshold: float = 0.6) -> Memory | None:
        """Near-duplicate detection by token overlap (no embeddings required)."""
        probe = tokenize(text)
        best, best_score = None, 0.0
        for m in self._items:
            score = overlap(probe, tokenize(m.text))
            if score > best_score:
                best, best_score = m, score
        return best if best_score >= threshold else None

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Memory]:
        return list(self._items)


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------


@dataclass
class Retrieved:
    memory: Memory
    score: float
    parts: dict


class Retriever:
    """Scores candidate memories and returns the top-k.

    score = kw*keyword_overlap + rc*recency + im*importance

    Weights are parameters rather than constants because the right balance is
    domain specific: a support agent wants recency, a research agent wants
    keyword precision.
    """

    def __init__(self, kw: float = 0.5, rc: float = 0.25, im: float = 0.25) -> None:
        total = kw + rc + im
        self.kw, self.rc, self.im = kw / total, rc / total, im / total

    def rank(
        self,
        query: str,
        sources: Iterable[tuple[Memory, float]],   # (memory, half_life)
        k: int = 4,
        now: float | None = None,
    ) -> list[Retrieved]:
        now = now or time.time()
        q = tokenize(query)
        scored: list[Retrieved] = []
        for mem, half_life in sources:
            kws = overlap(q, tokenize(mem.text))
            rcs = mem.recency(now, half_life)
            sc = self.kw * kws + self.rc * rcs + self.im * mem.importance
            scored.append(
                Retrieved(
                    memory=mem,
                    score=sc,
                    parts={
                        "keyword": round(kws, 3),
                        "recency": round(rcs, 3),
                        "importance": round(mem.importance, 3),
                    },
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        top = scored[:k]
        for r in top:
            r.memory.touch()
        return top


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[[dict], str]
    schema: dict


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def spec(self) -> str:
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)


def _tool_calculator(args: dict) -> str:
    """Evaluate a plain arithmetic expression (no eval on raw input)."""
    expr = str(args.get("expression", ""))
    if not re.fullmatch(r"[0-9+\-*/(). %]+", expr):
        return "error: only numbers and + - * / ( ) . % are allowed"
    try:
        # Safe: the character whitelist above is enforced first.
        value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
    return str(value)


def _tool_word_count(args: dict) -> str:
    text = str(args.get("text", ""))
    return str(len(text.split()))


def default_tools() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="calculator",
            description="Evaluate an arithmetic expression",
            func=_tool_calculator,
            schema={"expression": "string"},
        )
    )
    reg.register(
        Tool(
            name="word_count",
            description="Count words in a text",
            func=_tool_word_count,
            schema={"text": "string"},
        )
    )
    return reg


# --------------------------------------------------------------------------
# optional LLM adapter (stdlib only)
# --------------------------------------------------------------------------


class LLM:
    """Minimal OpenAI-compatible chat client over urllib.

    Absent a key, `available` is False and the agent uses its offline planner.
    """

    def __init__(self, model: str | None = None) -> None:
        self.key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.model = model or os.environ.get("LLM_MODEL", "deepseek-chat")

    @property
    def available(self) -> bool:
        return bool(self.key)

    def chat(self, system: str, user: str, timeout: float = 30.0) -> str:
        if not self.available:
            raise RuntimeError("no API key configured")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        return body["choices"][0]["message"]["content"].strip()


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------


class MemoryAgent:
    """Wires the three layers, retrieval and tools together."""

    def __init__(
        self,
        retriever: Retriever | None = None,
        tools: ToolRegistry | None = None,
        llm: LLM | None = None,
        working_capacity: int = 8,
    ) -> None:
        self.working = WorkingMemory(capacity=working_capacity)
        self.episodic = EpisodicMemory()
        self.longterm = LongTermMemory()
        self.retriever = retriever or Retriever()
        self.tools = tools or default_tools()
        self.llm = llm or LLM()
        self.trace: list[dict] = []

    # -- memory writes ----------------------------------------------------

    def remember(
        self,
        text: str,
        importance: float = 0.7,
        kind: str = "fact",
        tags: Iterable[str] = (),
    ) -> str:
        """Store a fact. Near-duplicates are merged instead of duplicated."""
        existing = self.longterm.find_near_duplicate(text)
        if existing is not None:
            # Reinforce rather than duplicate: repetition is a signal of importance.
            existing.importance = min(1.0, existing.importance + 0.1)
            existing.touch()
            return f"reinforced: {existing.text!r} (importance -> {existing.importance:.2f})"
        self.longterm.add(text, importance=importance, kind=kind, tags=tags)
        return f"stored: {text!r}"

    def note(self, text: str, importance: float = 0.4) -> Memory:
        """Record a session event. Returns the stored Memory (so callers can
        inspect or age it)."""
        return self.episodic.add(text, importance=importance)

    def consolidate(self, min_repeats: int = 2) -> list[str]:
        """Promote episodic events that repeat into durable long-term facts.

        This is the step most "memory" implementations skip, and it is the one
        that actually produces long-term behaviour.
        """
        groups: dict[frozenset, list[Memory]] = {}
        for m in self.episodic.all():
            groups.setdefault(frozenset(tokenize(m.text)), []).append(m)

        promoted: list[str] = []
        for key, items in groups.items():
            if len(items) < min_repeats or not key:
                continue
            text = items[0].text
            if self.longterm.find_near_duplicate(text) is None:
                self.longterm.add(text, importance=0.75, kind="fact", tags=("consolidated",))
                promoted.append(text)
        return promoted

    # -- retrieval --------------------------------------------------------

    def recall(self, query: str, k: int = 4) -> list[Retrieved]:
        sources: list[tuple[Memory, float]] = []
        sources += [(m, self.longterm.half_life) for m in self.longterm.all()]
        sources += [(m, self.episodic.half_life) for m in self.episodic.all()]
        return self.retriever.rank(query, sources, k=k)

    def build_context(self, query: str, k: int = 4) -> str:
        hits = self.recall(query, k=k)
        if not hits:
            return "(no relevant memory)"
        lines = []
        for r in hits:
            lines.append(f"[{r.memory.kind} {r.score:.2f}] {r.memory.text}")
        return "\n".join(lines)

    # -- one turn ---------------------------------------------------------

    def respond(self, user_input: str) -> str:
        """Answer one turn, recording a full decision trace."""
        self.working.add("user", user_input)
        self.note(f"user asked: {user_input}", importance=0.4)

        context = self.build_context(user_input, k=4)
        tool_used = None

        if self.llm.available:
            answer = self._respond_llm(user_input, context)
        else:
            answer, tool_used = self._respond_offline(user_input, context)

        self.working.add("assistant", answer)
        self.trace.append(
            {
                "input": user_input,
                "context": context,
                "tool": tool_used,
                "route": "llm" if self.llm.available else "offline",
                "answer": answer,
            }
        )
        return answer

    def _respond_offline(self, user_input: str, context: str) -> tuple[str, str | None]:
        """Deterministic planner so the demo runs with no API key."""
        lowered = user_input.lower()

        # tool routing
        if any(op in user_input for op in "+-*/") and re.search(r"\d", user_input):
            expr = re.sub(r"[^0-9+\-*/(). %]", "", user_input) or "0"
            out = _tool_calculator({"expression": expr})
            return f"[offline planner] calculator -> {out}", "calculator"

        if "how many words" in lowered or "数一下字数" in user_input:
            out = _tool_word_count({"text": user_input})
            return f"[offline planner] word_count -> {out}", "word_count"

        # memory recall reporting
        if any(k in lowered for k in ("what do you know", "remember", "记忆", "记得")):
            return f"[offline planner] relevant memory:\n{context}", None

        return (
            "[offline planner] no LLM key configured; "
            f"retrieved context for this turn:\n{context}",
            None,
        )

    def _respond_llm(self, user_input: str, context: str) -> str:
        system = (
            "You are a helpful assistant with access to retrieved memory about "
            "the user. Use the memory when it is relevant. If the memory does "
            "not contain the answer, say so plainly instead of inventing one.\n\n"
            f"Available tools:\n{self.tools.spec()}\n\n"
            f"Relevant memory:\n{context}\n\n"
            f"Recent turns:\n{self.working.render()}"
        )
        try:
            return self.llm.chat(system, user_input)
        except (urllib.error.URLError, RuntimeError, KeyError, TimeoutError) as exc:
            return f"[llm unavailable: {exc}] falling back to memory context:\n{context}"

    # -- introspection ----------------------------------------------------

    def stats(self) -> dict:
        return {
            "working_turns": len(self.working),
            "episodic": len(self.episodic),
            "longterm": len(self.longterm),
            "llm": self.llm.available,
            "model": self.llm.model if self.llm.available else None,
            "tools": self.tools.names(),
        }
