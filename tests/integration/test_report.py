import os

import pytest
from freezegun import freeze_time

import analyze
from tests.factories import small_raw_data

SECTIONS = ["cfdChart", "scatterChart", "activeWipAgeChart", "sizeCorrelationChart", "throughputChart", "prCometChartCanvas"]


@freeze_time("2024-03-01T00:00:00")
def test_report_writes_dashboard_with_all_charts_and_data():
    analyze.analyze_and_build_report(small_raw_data("acme/widgets"))
    html = open(analyze.DASHBOARD_FILE, encoding="utf-8").read()
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "GitHub Flow &amp; Value Stream Analytics" in html
    for canvas in SECTIONS:
        assert f'id="{canvas}"' in html
    assert "acme/widgets" in html
    assert "</html>" in html


@freeze_time("2024-03-01T00:00:00")
def test_report_with_no_prs_still_writes_a_file():
    analyze.analyze_and_build_report({"repo": "o/r"})
    assert os.path.getsize(analyze.DASHBOARD_FILE) > 1000


@freeze_time("2024-03-01T00:00:00")
def test_report_requires_repo_key_known_quirk():
    # Characterization: unlike compute_flow_metrics, the report writer does not default a missing "repo".
    with pytest.raises(KeyError, match="repo"):
        analyze.analyze_and_build_report({})


@freeze_time("2024-03-01T00:00:00")
def test_report_writes_only_into_the_temp_cwd(isolated_env):
    analyze.analyze_and_build_report(small_raw_data())
    assert (isolated_env / analyze.DASHBOARD_FILE).exists()
