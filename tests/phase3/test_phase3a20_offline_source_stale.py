"""Phase 3A20 fixture-backed source stale/freshness promotion.

This slice promotes only read-only ``source stale`` over committed synthetic
source fixtures. It does not promote source mutation/refresh, live RPC,
auth/browser/home reads, credentials, NotebookLM mutation, public sharing, or
parity rows.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"
SYNTHETIC_TEXT_SOURCE_ID = "fake-source-0002"


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def test_sources_api_check_freshness_returns_fixture_staleness_without_home(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    web_stale = asyncio.run(
        client.sources.check_freshness(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_SOURCE_ID)
    )
    text_stale = asyncio.run(
        client.sources.check_freshness(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_TEXT_SOURCE_ID)
    )

    assert web_stale is False
    assert text_stale is False


def test_source_service_marks_non_ready_fixture_rows_as_stale(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.sources import OfflineSourceService, Source, SourceStatus

    service = OfflineSourceService(
        {
            "notebook": [
                Source("ready", title="Ready", status=SourceStatus.READY),
                Source(
                    "processing", title="Processing", status=SourceStatus.PROCESSING
                ),
                Source("preparing", title="Preparing", status=SourceStatus.PREPARING),
                Source("error", title="Error", status=SourceStatus.ERROR),
            ]
        }
    )

    assert service.check_freshness("notebook", "ready") is False
    assert service.check_freshness("notebook", "processing") is True
    assert service.check_freshness("notebook", "preparing") is True
    assert service.check_freshness("notebook", "error") is True


def test_sources_api_check_freshness_missing_source_is_redacted(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens
    from notebooklm.errors import ValidationError

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))
    secret = "ya" + "29." + "F" * 40
    raw_path = "/".join(("", "Users", "example", "stale"))

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(
            client.sources.check_freshness(
                SYNTHETIC_NOTEBOOK_ID, f"missing {secret} {raw_path}"
            )
        )

    message = str(excinfo.value)
    assert message == "source not found"
    assert secret not in message
    assert raw_path not in message
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_cli_source_stale_json_uses_standard_success_exit_code(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "stale",
            SYNTHETIC_SOURCE_ID,
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "title": "Synthetic Web Source",
        "stale": False,
        "status": "READY",
        "kind": "WEB_PAGE",
        "url": "https://example.test/notebooklm-bare/source",
        "basis": "offline_fixture_status",
    }


def test_cli_source_stale_plain_output_is_human_readable(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["source", "stale", SYNTHETIC_SOURCE_ID, "-n", "fake-notebook"]
    )

    assert code == 0
    assert err == ""
    assert "Source Synthetic Web Source is fresh" in out
    assert "offline fixture status" in out


def test_cli_source_stale_exit_on_stale_uses_predicate_exit_code(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["source", "stale", SYNTHETIC_SOURCE_ID, "--exit-on-stale", "--json"],
    )

    assert code == 1
    assert err == ""
    assert json.loads(out)["stale"] is False


def test_cli_source_stale_missing_source_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "S" * 40
    raw_path = "/".join(("", "Users", "example", "stale"))
    private_selector = f"missing {secret} {raw_path}"

    code, out, err = _run(mods, capsys, ["source", "stale", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "source not found" in err
    assert secret not in err
    assert raw_path not in err


def test_phase3a20_source_stale_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    stale_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm source stale"
    )
    opts = {
        opt
        for param in stale_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {"-n", "--notebook", "--exit-on-stale", "--json"}


def test_phase3a20_live_source_stale_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["source", "stale", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {"-n", "--notebook", "--exit-on-stale", "--json"}:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a20_source_stale_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/sources.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "sources.py")
    )
    forbidden = {
        "sock" + "et",
        "http" + ".client",
        "urllib" + ".request",
        "url" + "open",
        "sub" + "process",
        "Path" + ".home",
        "expand" + "user",
        "os" + ".environ",
        "browser" + "_cookies",
        "interactive" + "_login",
        "Network" + ".",
        "Dev" + "Tools",
        "key" + "ring",
        "secret" + "storage",
        "win32" + "crypt",
        "browser" + "_cookie3",
        "browser" + "cookie",
    }
    assert sorted(token for token in forbidden if token in src) == []
