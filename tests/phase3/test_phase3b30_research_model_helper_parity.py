"""Research model helper parity for pinned upstream typed returns."""

from __future__ import annotations


def test_research_source_public_dict_helpers_match_upstream():
    from notebooklm import ResearchSource

    source = ResearchSource.from_public_dict(
        {
            "url": 42,
            "title": None,
            "result_type": "report",
            "research_task_id": "research-row-one",
            "report_markdown": 123,
        }
    )

    assert source.url == ""
    assert source.title == "Untitled"
    assert source.result_type == 5
    assert source.research_task_id == "research-row-one"
    assert source.report_markdown == ""
    assert source.is_report is True

    updated = source.with_report_markdown("# Report")
    assert updated is not source
    assert updated.report_markdown == "# Report"
    assert source.report_markdown == ""
    assert updated.to_public_dict()["report_markdown"] == "# Report"


def test_source_guide_keywords_are_tuple_stored_and_list_exported():
    from notebooklm import SourceGuide

    guide = SourceGuide(summary="Summary", keywords=["alpha", "beta"])

    assert guide.keywords == ("alpha", "beta")
    public = guide.to_public_dict()
    assert public == {"summary": "Summary", "keywords": ["alpha", "beta"]}
    public["keywords"].append("mutated")
    assert guide.keywords == ("alpha", "beta")
