"""Retrieval over the pet-care knowledge base.

The knowledge base is a folder of Markdown files. Each `## RULE-ID: Title` heading
starts one retrievable rule. Rule IDs are stable and machine-checkable, which is what
lets the critic verify that a plan cited a rule that actually exists (see
`pawpal_ai.planner.verify_citations`).

Scoring is BM25 implemented on the standard library only: no network, no model
download, fully deterministic. That keeps retrieval reproducible in tests and in
grading, and keeps the dependency list short.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default knowledge directory, resolved relative to the repo root.
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# `## MED-001: Twice-daily medications need ~12 hours between doses`
_HEADING = re.compile(r"^##\s+([A-Z]+-\d+):\s*(.+?)\s*$", re.MULTILINE)
_TOKEN = re.compile(r"[a-z0-9]+")

# Very common words carry no signal for ranking and would otherwise let a long
# query match every rule equally.
_STOPWORDS = frozenset(
    """a an the and or but if is are was were be been being of to in on at for with
    about into over after before between out against during without within along
    my our your their its it this that these those i you he she we they them do does
    did doing have has had having can could should would will shall may might must
    not no nor so than then there here when where which who whom what how why all any
    both each few more most other some such only own same too very s t just don now""".split()
)


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords.

    Rule IDs like `MED-001` become `med` + `001`, so a query naming a rule ID still
    retrieves that rule.
    """
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Rule:
    """One retrievable unit of pet-care guidance."""

    rule_id: str
    title: str
    body: str
    source: str

    @property
    def text(self) -> str:
        """The full text a model should see when this rule is retrieved."""
        return f"{self.rule_id}: {self.title}\n{self.body}"

    def __str__(self) -> str:
        return self.text


@dataclass
class RetrievedRule:
    """A rule plus the score that caused it to be retrieved."""

    rule: Rule
    score: float


@dataclass
class KnowledgeBase:
    """A BM25-searchable collection of pet-care rules.

    Build one with :meth:`load`, then call :meth:`search`.
    """

    rules: list[Rule] = field(default_factory=list)

    # BM25 free parameters. k1 controls term-frequency saturation, b controls how
    # much a rule's length is penalised. These are the standard defaults.
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._index: list[Counter[str]] = [Counter(_tokenize(r.text)) for r in self.rules]
        self._lengths = [sum(c.values()) for c in self._index]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        self._by_id = {r.rule_id: r for r in self.rules}

        # Document frequency: how many rules contain each term.
        df: Counter[str] = Counter()
        for counts in self._index:
            df.update(counts.keys())
        self._df = df

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls, directory: Path | str | None = None) -> "KnowledgeBase":
        """Parse every ``.md`` file in *directory* into rules.

        Raises:
            FileNotFoundError: if the directory does not exist. A silently empty
                knowledge base would make the whole RAG layer a no-op, so this is
                surfaced loudly rather than tolerated.
        """
        directory = Path(directory) if directory else DEFAULT_KNOWLEDGE_DIR
        if not directory.is_dir():
            raise FileNotFoundError(f"Knowledge directory not found: {directory}")

        rules: list[Rule] = []
        for path in sorted(directory.glob("*.md")):
            rules.extend(cls._parse_file(path))

        if not rules:
            raise ValueError(
                f"No rules parsed from {directory}. Rules must use '## RULE-ID: Title' headings."
            )

        logger.info("Loaded %d rules from %s", len(rules), directory)
        return cls(rules=rules)

    @staticmethod
    def _parse_file(path: Path) -> list[Rule]:
        """Split one Markdown file into rules on its ``## ID: Title`` headings."""
        text = path.read_text(encoding="utf-8")
        matches = list(_HEADING.finditer(text))

        rules = []
        for i, match in enumerate(matches):
            # Body runs from the end of this heading to the start of the next one.
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip()
            rules.append(
                Rule(
                    rule_id=match.group(1),
                    title=match.group(2),
                    body=body,
                    source=path.name,
                )
            )
        return rules

    # -------------------------------------------------------------- retrieval

    def search(self, query: str, k: int = 6) -> list[RetrievedRule]:
        """Return the *k* highest-scoring rules for *query*, best first.

        Rules scoring zero are dropped rather than padded in — returning irrelevant
        rules just to fill the quota would dilute the grounding context.
        """
        terms = _tokenize(query)
        if not terms or not self.rules:
            return []

        n = len(self.rules)
        avg = self._avg_length or 1.0  # guard against an all-empty corpus
        scored: list[RetrievedRule] = []

        for i, rule in enumerate(self.rules):
            counts = self._index[i]
            length = self._lengths[i]
            score = 0.0

            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                # Standard BM25 IDF, with the +1 that keeps it non-negative.
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                norm = 1 - self.b + self.b * (length / avg)
                score += idf * (tf * (self.k1 + 1)) / (tf + self.k1 * norm)

            if score > 0:
                scored.append(RetrievedRule(rule=rule, score=score))

        scored.sort(key=lambda r: (-r.score, r.rule.rule_id))
        return scored[:k]

    def get(self, rule_id: str) -> Rule | None:
        """Look up a rule by ID. Returns ``None`` if it does not exist."""
        return self._by_id.get(rule_id.strip().upper())

    def exists(self, rule_id: str) -> bool:
        """Whether *rule_id* names a real rule. Used to validate model citations."""
        return self.get(rule_id) is not None

    @property
    def rule_ids(self) -> list[str]:
        """Every known rule ID, sorted."""
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self.rules)


def format_context(retrieved: list[RetrievedRule]) -> str:
    """Render retrieved rules as the grounding block shown to the model."""
    if not retrieved:
        return "(no relevant guidance found in the knowledge base)"
    return "\n\n".join(f"[{r.rule.source}] {r.rule.text}" for r in retrieved)
