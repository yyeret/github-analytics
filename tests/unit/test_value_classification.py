import pytest

from value_classification import classify_content, summarize_items


@pytest.mark.parametrize('title,body,level', [
    ('Investigate intermittent timeout', '', 'Activity'),
    ('fix: API crashes', '', 'Activity'),
    ('Build reporting dashboard', '', 'Output'),
    ('Add CSV export', 'Users can download their own transaction history.', 'Outcome'),
    ('Enable SSL', '', 'Activity'),
    ('Increase conversion by 10%', '', 'Impact'),
    ('Revenue Report ingested via CSV', '', 'Activity'),
    ('Allocate headcount', '', 'Input'),
    ('FY26Q4 reporting dashboard', '', 'Activity'),
    ('', '', 'Unknown'),
])
def test_shared_taxonomy_examples(title, body, level):
    result = classify_content({'title': title, 'body': body})
    assert result['level'] == level
    assert result['rationale']
    assert result['method'] == 'rules'


def test_body_can_supply_outcome_but_bug_label_alone_does_not_override_content():
    result = classify_content({'title': 'Support exports', 'body': 'Users can export their data.',
                               'labels': {'nodes': [{'name': 'bug'}]}})
    assert result['level'] == 'Outcome'


def test_legacy_title_only_content_reports_missing_body_and_default():
    result = classify_content({'title': 'Zorg'})
    assert result['level'] == 'Activity'
    assert result['defaulted'] is True
    assert result['body_available'] is False


def test_summary_deduplicates_and_preserves_item_rationale():
    item = {'number': 1, 'title': 'Build dashboard', 'body': 'A chart.'}
    out = summarize_items([('open', [item]), ('closed', [item])])
    assert out['total'] == 1
    assert out['counts']['Output'] == 1
    assert out['body_available'] == 1
    assert out['items'][0]['number'] == 1
    assert out['items'][0]['rationale']
