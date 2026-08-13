from datetime import date

import scripts.build_profile_dashboard as bpd


def test_build_writes_readme_and_svg(tmp_path) -> None:
    readme = tmp_path / "README.md"
    svg = tmp_path / "assets" / "tomax" / "dashboard.svg"

    changed = bpd.build(
        data_dir=tmp_path / "data" / "v1" / "devices",
        readme_path=readme,
        dashboard_svg_path=svg,
        today=date(2026, 7, 24),
        generated_at="2026-07-24 00:00 UTC",
    )

    assert changed is True
    assert svg.read_text(encoding="utf-8").startswith("<svg")
    assert "assets/tomax/dashboard.svg" in readme.read_text(encoding="utf-8")
