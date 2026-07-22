"""Public RPC module surface parity."""

from __future__ import annotations

import ast
from pathlib import Path

REFERENCE_ROOT = Path("notebooklm-py-reference/src/notebooklm")


def _upstream_all(relative: str) -> list[str]:
    tree = ast.parse((REFERENCE_ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{relative} has no __all__")


def test_rpc_package_all_matches_upstream():
    import notebooklm.rpc as rpc

    expected = _upstream_all("rpc/__init__.py")

    assert rpc.__all__ == expected
    assert all(hasattr(rpc, name) for name in expected)


def test_rpc_decoder_all_and_exception_reexports_match_upstream():
    import notebooklm.exceptions as exceptions
    import notebooklm.rpc.decoder as decoder

    expected = _upstream_all("rpc/decoder.py")

    assert decoder.__all__ == expected
    assert decoder.RPCError is exceptions.RPCError
    assert decoder.AuthError is exceptions.AuthError
    assert decoder.UnknownRPCMethodError is exceptions.UnknownRPCMethodError


def test_rpc_encoder_and_types_do_not_define_dunder_all_like_upstream():
    import notebooklm.rpc.encoder as encoder
    import notebooklm.rpc.types as types

    assert not hasattr(encoder, "__all__")
    assert not hasattr(types, "__all__")


def test_rpc_enum_aliases_and_source_id_nesting_match_upstream():
    import pytest

    from notebooklm.rpc.encoder import nest_source_ids
    from notebooklm.rpc.types import ArtifactTypeCode, QuizQuantity

    assert ArtifactTypeCode.QUIZ_FLASHCARD.value == 4
    assert ArtifactTypeCode.QUIZ_FLASHCARD is ArtifactTypeCode.QUIZ
    assert QuizQuantity.MORE.value == 2
    assert QuizQuantity.MORE is QuizQuantity.STANDARD

    assert nest_source_ids(["a", "b"], 2) == [[["a"]], [["b"]]]
    assert nest_source_ids(None, 1) == []
    with pytest.raises(ValueError, match="depth must be >= 1, got 0"):
        nest_source_ids(["a"], 0)
