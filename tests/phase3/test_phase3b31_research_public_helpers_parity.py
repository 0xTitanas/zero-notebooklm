"""Public research helper parity for cited-source selection."""

from __future__ import annotations


def test_research_url_normalization_and_extraction_match_upstream():
    from notebooklm import research

    assert research.__all__ == [
        "extract_report_urls",
        "normalize_citation_url",
        "normalize_url",
        "select_cited_sources",
    ]
    assert not hasattr(research, "ResearchAPI")

    assert (
        research.normalize_citation_url("HTTPS://Example.COM/path/?q=1#Frag.")
        == "https://example.com/path?q=1#Frag"
    )
    assert research.normalize_url("HTTPS://Example.COM/path/") == "https://example.com/path"

    report = " ".join(
        [
            "See [Alpha](https://Example.COM/a/?q=1#Frag).",
            "Bare https://Example.com/b/.",
            "Ignore image ![Alt](https://example.com/image.png).",
        ]
    )

    assert research.extract_report_urls(report) == {
        "https://example.com/a?q=1#Frag",
        "https://example.com/b",
    }


def test_select_cited_sources_preserves_report_and_falls_back_like_upstream():
    from notebooklm import research

    report_source = {
        "title": "Report",
        "result_type": "report",
        "report_markdown": "# Report",
    }
    alpha = {"url": "https://example.com/a/", "title": "Alpha"}
    beta = {"url": "https://example.com/b", "title": "Beta"}
    sources = [alpha, report_source, beta]

    selected = research.select_cited_sources(sources, "Cited: https://example.com/b.")

    assert selected.sources == [report_source, beta]
    assert selected.cited_url_count == 1
    assert selected.matched_url_source_count == 1
    assert selected.used_fallback is False

    fallback = research.select_cited_sources(sources, "No URLs here.")
    assert fallback.sources == sources
    assert fallback.cited_url_count == 0
    assert fallback.matched_url_source_count == 0
    assert fallback.used_fallback is True
