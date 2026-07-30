from datetime import date

from tomax.dashboard import payload as payload_module


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

    def fake_fetch(repo_target, *, branch="main"):
        captured["repo_target"] = repo_target
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

    result = payload_module.build_payload(
        ledger_path=tmp_path / "ledger.sqlite3",
        all_devices=True,
        repo_target="owner/repo",
        privacy_policy=payload_module.PrivacyPolicy(),
        today=date(2026, 7, 11),
        pie_top_n=6,
        tmp_stage_dir=tmp_path / "stage",
    )

    assert result == {"ok": True}
    assert captured["repo_target"] == "owner/repo"
    assert captured["valid_payloads"] == [valid_entry]
