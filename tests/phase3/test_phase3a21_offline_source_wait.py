"""Phase 3A21 fixture-backed source wait promotion.

This slice promotes only read-only/offline ``source wait`` and the fixture-backed
``SourcesAPI.wait_*`` helpers. It does not promote source mutation/refresh, live
RPC, auth/browser/home reads, credentials, NotebookLM mutation, public sharing,
or parity rows.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"
PROCESSING_SOURCE_ID = "fake-source-processing"
ERROR_SOURCE_ID = "fake-source-error"


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


def _fixture_dir_with_sources(repo_root, tmp_path, rows):
    fixture_dir = tmp_path / "rpc_fixtures"
    shutil.copytree(repo_root / "compat" / "rpc_fixtures", fixture_dir)
    inner = json.dumps(rows, separators=(",", ":"))
    outer = [["wrb.fr", "list-sources-rpc", inner, None, None, None, "generic"]]
    (fixture_dir / "list_sources.response.txt").write_text(
        ")]}'\n" + json.dumps(outer, separators=(",", ":")),
        encoding="utf-8",
    )
    return fixture_dir


def test_sources_api_wait_until_ready_returns_ready_source_without_home(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.auth import AuthTokens
    from notebooklm.client import NotebookLMClient

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    source = asyncio.run(
        client.sources.wait_until_ready(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_SOURCE_ID)
    )

    assert source.id == SYNTHETIC_SOURCE_ID
    assert source.status.name == "READY"
    assert source.title == "Synthetic Web Source"


def test_offline_source_service_wait_uses_fixture_status_without_sleep(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.errors import ValidationError
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

    assert service.wait_until_ready("notebook", "ready", timeout=1).id == "ready"
    with pytest.raises(TimeoutError) as processing:
        service.wait_until_ready("notebook", "processing", timeout=1)
    assert str(processing.value) == "source is still processing in offline fixture"
    with pytest.raises(TimeoutError):
        service.wait_until_ready("notebook", "preparing", timeout=1)
    with pytest.raises(ValidationError) as failed:
        service.wait_until_ready("notebook", "error", timeout=1)
    assert str(failed.value) == "source processing failed"


def test_sources_api_wait_for_sources_returns_all_ready_sources(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.auth import AuthTokens
    from notebooklm.client import NotebookLMClient

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    sources = asyncio.run(
        client.sources.wait_for_sources(
            SYNTHETIC_NOTEBOOK_ID,
            ["fake-source-0001", "fake-source-0002"],
            timeout=1,
        )
    )

    assert [source.id for source in sources] == ["fake-source-0001", "fake-source-0002"]
    assert {source.status.name for source in sources} == {"READY"}


def test_sources_api_wait_until_ready_missing_source_is_redacted(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.auth import AuthTokens
    from notebooklm.client import NotebookLMClient
    from notebooklm.errors import ValidationError

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))
    secret = "ya" + "29." + "W" * 40
    raw_path = "/".join(("", "Users", "example", "wait"))

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(
            client.sources.wait_until_ready(
                SYNTHETIC_NOTEBOOK_ID, f"missing {secret} {raw_path}"
            )
        )

    message = str(excinfo.value)
    assert message == "source not found"
    assert secret not in message
    assert raw_path not in message
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_cli_source_wait_ready_json_uses_success_exit_code(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "wait",
            SYNTHETIC_SOURCE_ID,
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "title": "Synthetic Web Source",
        "ready": True,
        "status": "READY",
        "kind": "WEB_PAGE",
        "url": "https://example.test/notebooklm-bare/source",
        "timed_out": False,
        "failed": False,
        "basis": "offline_fixture_status",
    }


def test_cli_source_wait_plain_output_is_human_readable(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["source", "wait", SYNTHETIC_SOURCE_ID, "-n", "fake-notebook"]
    )

    assert code == 0
    assert err == ""
    assert "Source Synthetic Web Source is ready" in out
    assert "offline fixture status" in out


def test_cli_source_wait_processing_fixture_returns_timeout_exit_code(
    repo_root, tmp_path, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    fixture_dir = _fixture_dir_with_sources(
        repo_root,
        tmp_path,
        [
            [PROCESSING_SOURCE_ID, "Processing Source", None, 2, 1750000300, 1],
        ],
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "wait",
            PROCESSING_SOURCE_ID,
            "--fixture-dir",
            str(fixture_dir),
            "--timeout",
            "1",
            "--interval",
            "1",
            "--json",
        ],
    )

    assert code == 2
    assert err == ""
    data = json.loads(out)
    assert data["source_id"] == PROCESSING_SOURCE_ID
    assert data["status"] == "PROCESSING"
    assert data["ready"] is False
    assert data["timed_out"] is True
    assert data["failed"] is False
    assert data["basis"] == "offline_fixture_status"


def test_cli_source_wait_error_fixture_returns_failure_exit_code(
    repo_root, tmp_path, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    fixture_dir = _fixture_dir_with_sources(
        repo_root,
        tmp_path,
        [
            [ERROR_SOURCE_ID, "Errored Source", None, 2, 1750000400, 3],
        ],
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "wait",
            ERROR_SOURCE_ID,
            "--fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )

    assert code == 1
    assert err == ""
    data = json.loads(out)
    assert data["source_id"] == ERROR_SOURCE_ID
    assert data["status"] == "ERROR"
    assert data["ready"] is False
    assert data["timed_out"] is False
    assert data["failed"] is True


def test_cli_source_wait_missing_source_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "Q" * 40
    raw_path = "/".join(("", "Users", "example", "wait"))
    private_selector = f"missing {secret} {raw_path}"

    code, out, err = _run(mods, capsys, ["source", "wait", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "source not found" in err
    assert secret not in err
    assert raw_path not in err


def test_cli_source_wait_rejects_invalid_polling_values(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    for argv in (
        ["source", "wait", SYNTHETIC_SOURCE_ID, "--timeout", "0"],
        ["source", "wait", SYNTHETIC_SOURCE_ID, "--interval", "0"],
    ):
        code, out, err = _run(mods, capsys, argv)
        assert code == 64
        assert out == ""
        assert "must be positive" in err


def test_phase3a21_source_wait_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    wait_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm source wait"
    )
    opts = {
        opt
        for param in wait_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {"-n", "--notebook", "--timeout", "--interval", "--json"}


def test_phase3a21_live_source_wait_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["source", "wait", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {"-n", "--notebook", "--timeout", "--interval", "--json"}:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a21_source_wait_wiring_is_stdlib_and_offline_only(repo_root):
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
