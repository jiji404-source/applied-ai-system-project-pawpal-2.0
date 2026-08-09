"""Tests for knowledge-base loading and BM25 retrieval.

These run entirely offline — retrieval is pure Python with no model behind it, which
is exactly why it can be pinned down this precisely.
"""

import pytest

from pawpal_ai.retriever import KnowledgeBase, Rule, format_context


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase.load()


def test_knowledge_base_loads_rules(kb):
    # ARRANGE / ACT: loading happens in the fixture
    # ASSERT: the real knowledge base parsed into a meaningful number of rules
    assert len(kb) >= 30
    assert "MED-002" in kb.rule_ids
    assert "FEED-004" in kb.rule_ids


def test_rules_carry_title_body_and_source(kb):
    rule = kb.get("MED-002")
    assert rule is not None
    assert "allergy" in rule.title.lower()
    # Source Markdown is hard-wrapped, so normalise whitespace before matching.
    assert "empty stomach" in " ".join(rule.body.lower().split())
    assert rule.source == "medication_timing.md"


def test_lookup_is_case_insensitive_and_rejects_unknown_ids(kb):
    # Citation checking depends on this: the model may return 'med-002'.
    assert kb.exists("med-002")
    assert kb.exists("MED-002")
    # An invented ID must not validate — this is the hallucination guardrail.
    assert not kb.exists("MED-999")
    assert kb.get("NOPE-001") is None


def test_search_ranks_the_on_topic_rule_first(kb):
    # ARRANGE: a query about giving an allergy pill around mealtimes
    results = kb.search("when do I give the allergy pill relative to food", k=3)

    # ASSERT: MED-002 is the rule that governs this, and it should win
    assert results, "expected at least one result"
    assert results[0].rule.rule_id == "MED-002"
    assert results[0].score > 0


def test_search_surfaces_the_safety_rule_for_emergency_wording(kb):
    results = kb.search("my dog collapsed and is bloating", k=3)
    assert "SAFE-003" in [r.rule.rule_id for r in results]


def test_search_respects_k_and_drops_zero_scoring_rules(kb):
    assert len(kb.search("feeding", k=2)) <= 2
    # Nothing in the corpus is about this, so nothing should be returned rather
    # than the top-k padded out with irrelevant rules.
    assert kb.search("quantum chromodynamics tensor") == []


def test_search_handles_empty_and_stopword_only_queries(kb):
    assert kb.search("") == []
    assert kb.search("the and of to") == []


def test_missing_directory_raises_rather_than_returning_empty(tmp_path):
    # A silently empty knowledge base would turn the whole RAG layer into a no-op,
    # so this must fail loudly.
    with pytest.raises(FileNotFoundError):
        KnowledgeBase.load(tmp_path / "does-not-exist")


def test_directory_with_no_parseable_rules_raises(tmp_path):
    (tmp_path / "notes.md").write_text("# Heading only\n\nNo rule headings here.")
    with pytest.raises(ValueError):
        KnowledgeBase.load(tmp_path)


def test_parses_multiple_rules_from_one_file(tmp_path):
    (tmp_path / "kb.md").write_text(
        "# Test\n\n"
        "## AAA-001: First rule\nBody of the first rule.\n\n"
        "## AAA-002: Second rule\nBody of the second rule.\n"
    )
    loaded = KnowledgeBase.load(tmp_path)
    assert loaded.rule_ids == ["AAA-001", "AAA-002"]
    assert "first rule" in loaded.get("AAA-001").body.lower()


def test_format_context_labels_each_rule_with_its_source(kb):
    rendered = format_context(kb.search("allergy pill with food", k=2))
    assert "medication_timing.md" in rendered
    assert "MED-002" in rendered


def test_format_context_handles_no_results():
    assert "no relevant guidance" in format_context([]).lower()


def test_rule_text_includes_id_and_title():
    rule = Rule(rule_id="X-1", title="A title", body="A body.", source="x.md")
    assert rule.text.startswith("X-1: A title")
    assert "A body." in rule.text
