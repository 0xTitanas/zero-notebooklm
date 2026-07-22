"""Root module public attribute parity for Zero-only re-export removal."""

from __future__ import annotations


def test_root_module_omits_zero_only_subclient_reexports_like_upstream():
    import notebooklm

    zero_only_names = {
        "ArtifactStatus",
        "ArtifactsAPI",
        "ChatAPI",
        "MindMapsAPI",
        "NotebooksAPI",
        "NotesAPI",
        "ResearchAPI",
        "SettingsAPI",
        "SharingAPI",
        "SourcesAPI",
    }

    assert zero_only_names.isdisjoint(vars(notebooklm))


def test_root_module_all_matches_upstream_order(repo_root):
    import ast
    import notebooklm

    tree = ast.parse(
        (repo_root / "notebooklm-py-reference/src/notebooklm/__init__.py").read_text(
            encoding="utf-8"
        )
    )
    upstream_all = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    upstream_all = ast.literal_eval(node.value)
                    break
    assert upstream_all is not None
    assert notebooklm.__all__ == upstream_all
