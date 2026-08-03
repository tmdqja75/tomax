from datetime import date

from tomax.dashboard import payload as payload_module
from tomax.ledger.repository import LedgerRepository


def test_build_payload_remote_uses_fetched_entries(monkeypatch, tmp_path):
    valid_entry = {
        "schema_version": 1,
        "device_id": "devA",
        "date": "2026-07-10",
        "agents": {},
        "skills": {},
        "mcp_servers": {},
        "mcp_tools": {},
    }
    captured = {}

    def fake_fetch(repo_target, *, branch="main", exclude_device_id=None):
        captured["repo_target"] = repo_target
        captured["exclude_device_id"] = exclude_device_id
        return [("devA", valid_entry)]

    def fake_partition(entries, *, today):
        captured["entries"] = entries

        class R:
            valid_payloads = [valid_entry]

        return R()

    def fake_build(
        valid_payloads, *, today, pie_top_n, bar_chart_threshold_days, include_cache_tokens
    ):
        captured["valid_payloads"] = valid_payloads
        return {"ok": True}

    monkeypatch.setattr(payload_module, "fetch_device_entries", fake_fetch)
    monkeypatch.setattr(payload_module, "validate_and_partition", fake_partition)
    monkeypatch.setattr(payload_module, "build_dashboard_data", fake_build)

    ledger_path = tmp_path / "ledger.sqlite3"
    repository = LedgerRepository.open(ledger_path)
    local_device_id = repository.get_or_create_device_id()
    repository.close()

    result = payload_module.build_payload(
        ledger_path=ledger_path,
        all_devices=True,
        repo_target="owner/repo",
        privacy_policy=payload_module.PrivacyPolicy(),
        today=date(2026, 7, 11),
        pie_top_n=6,
        tmp_stage_dir=tmp_path / "stage",
    )

    assert result == {"ok": True}
    assert captured["repo_target"] == "owner/repo"
    assert captured["exclude_device_id"] == local_device_id
    assert captured["valid_payloads"] == [valid_entry]
    # this device has no local records yet, so entries are remote-only
    assert captured["entries"] == [("devA", valid_entry)]


def test_build_payload_local_only_skips_remote_fetch(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("fetch_device_entries must not be called without --all-devices")

    def fake_partition(entries, *, today):
        class R:
            valid_payloads = []

        return R()

    def fake_build(
        valid_payloads, *, today, pie_top_n, bar_chart_threshold_days, include_cache_tokens
    ):
        return {"ok": True, "count": len(valid_payloads)}

    monkeypatch.setattr(payload_module, "fetch_device_entries", boom)
    monkeypatch.setattr(payload_module, "validate_and_partition", fake_partition)
    monkeypatch.setattr(payload_module, "build_dashboard_data", fake_build)

    result = payload_module.build_payload(
        ledger_path=tmp_path / "ledger.sqlite3",
        all_devices=False,
        repo_target=None,
        privacy_policy=payload_module.PrivacyPolicy(),
        today=date(2026, 7, 11),
        pie_top_n=6,
        tmp_stage_dir=tmp_path / "stage",
    )

    assert result == {"ok": True, "count": 0}


def test_build_payload_claude_code_only_range_trims_to_claude_code_availability(
    monkeypatch, tmp_path
):
    payloads = [
        {
            "date": "2026-07-01",
            "agents": {"codex": {"source_status": "available_with_activity"}},
        },
        {
            "date": "2026-07-05",
            "agents": {"claude_code": {"source_status": "available_with_activity"}},
        },
        {
            "date": "2026-07-10",
            "agents": {"claude_code": {"source_status": "available_with_activity"}},
        },
        {
            "date": "2026-07-15",
            "agents": {"codex": {"source_status": "available_with_activity"}},
        },
    ]

    def fake_partition(entries, *, today):
        class R:
            valid_payloads = payloads

        return R()

    def fake_build(
        valid_payloads, *, today, pie_top_n, bar_chart_threshold_days, include_cache_tokens
    ):
        return {"dates": [p["date"] for p in valid_payloads]}

    monkeypatch.setattr(payload_module, "validate_and_partition", fake_partition)
    monkeypatch.setattr(payload_module, "build_dashboard_data", fake_build)

    result = payload_module.build_payload(
        ledger_path=tmp_path / "ledger.sqlite3",
        all_devices=False,
        repo_target=None,
        privacy_policy=payload_module.PrivacyPolicy(),
        today=date(2026, 7, 16),
        pie_top_n=6,
        tmp_stage_dir=tmp_path / "stage",
        claude_code_only_range=True,
    )

    assert result == {"dates": ["2026-07-05", "2026-07-10"]}


def test_build_payload_claude_code_only_range_is_off_by_default(monkeypatch, tmp_path):
    payloads = [{"date": "2026-07-01", "agents": {"codex": {"source_status": "available_with_activity"}}}]

    def fake_partition(entries, *, today):
        class R:
            valid_payloads = payloads

        return R()

    def fake_build(
        valid_payloads, *, today, pie_top_n, bar_chart_threshold_days, include_cache_tokens
    ):
        return {"dates": [p["date"] for p in valid_payloads]}

    monkeypatch.setattr(payload_module, "validate_and_partition", fake_partition)
    monkeypatch.setattr(payload_module, "build_dashboard_data", fake_build)

    result = payload_module.build_payload(
        ledger_path=tmp_path / "ledger.sqlite3",
        all_devices=False,
        repo_target=None,
        privacy_policy=payload_module.PrivacyPolicy(),
        today=date(2026, 7, 2),
        pie_top_n=6,
        tmp_stage_dir=tmp_path / "stage",
    )

    assert result == {"dates": ["2026-07-01"]}
