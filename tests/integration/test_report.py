import json
import os

import pytest
from freezegun import freeze_time

import analyze
from tests.factories import make_issue, make_pr, small_raw_data

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


@freeze_time("2024-01-09T00:00:00")
def test_api_and_report_preserve_pr_flow_series_boundaries(isolated_env):
    data = {
        "repo": "o/r",
        "recent_prs": [
            make_pr(1, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"),
            make_pr(2, "2024-01-04T00:00:00Z", "2024-01-09T00:00:00Z"),
            # A reversed merge interval still records a merge and its pre-creation coding window.
            make_pr(3, "2024-01-09T02:00:00Z", "2024-01-09T00:00:00Z", login="dependabot"),
        ],
        "open_prs": [
            make_pr(4, "2024-01-09T02:00:00Z", None, issues=[make_issue()]),
            # A finish in the open cohort ends review, without adding to completed throughput.
            make_pr(5, "2024-01-02T00:00:00Z", "2024-01-09T00:00:00Z"),
        ],
    }
    metrics = analyze.compute_flow_metrics(data)
    analyze.analyze_and_build_report(data)
    html = (isolated_env / analyze.DASHBOARD_FILE).read_text(encoding="utf-8")

    def chart_data(name):
        return json.loads(html.split(f"const {name} = ", 1)[1].split(";\n", 1)[0])

    for field, variable, expected in (
        ("month", "cfdMonths", ["2024-01-02", "2024-01-09", "2024-01-09"]),
        ("merged", "cfdMerged", [1, 3, 3]),
        ("review", "cfdReview", [1, 0, 0]),
        ("coding", "cfdCoding", [0, 2, 2]),
        ("backlog", "cfdBacklog", [1, 0, 0]),
    ):
        assert [row[field] for row in metrics["cfd_data"]] == chart_data(variable) == expected
    assert metrics["throughput_data"] == chart_data("throughputData") == [
        {"week": "2024-01-09", "human": 1, "assisted": 0, "agentic": 1, "total": 2},
        {"week": "2024-01-09", "human": 0, "assisted": 0, "agentic": 0, "total": 0},
    ]
