import asyncio
import json

import pytest

import product_value as pv
from tests.integration.test_product_value import CONTEXT, FakeServices, jev_reply


class IssueServices(FakeServices):
    async def __call__(self, url, **kwargs):
        if 'typesafe.ai' in url:
            body = kwargs['body']
            self.jev_requests.append(body)
            number = body['state']['issue']['number']
            return 200, {}, self.assessments.get(number, jev_reply())
        return await super().__call__(url, **kwargs)


def test_issue_titles_proposals_ranking_and_indeterminate(monkeypatch):
    uncertain = jev_reply()
    uncertain['answers']['intent_assessable']['noul'] = .5
    fake = IssueServices(assessments={3: uncertain})
    monkeypatch.setattr(pv, 'request_json', fake)
    result = asyncio.run(pv.assess_issue_value('o/r', [
        {'number': 2, 'title': 'Add 2FA', 'body': ''},
        {'number': 1, 'title': 'a' * 210, 'body': 'b' * 1600},
        {'number': 3, 'title': 'Various improvements', 'body': ''}], 'gemini', jev_key='jev'))
    assert result['assessed'] == 2 and result['failed'] == 1
    assert [entry['number'] for entry in result['ranking']] == [1, 2]
    assert result['total_issues'] == 3
    states = {r['state']['issue']['number']: r for r in fake.jev_requests}
    assert states[2]['state']['issue']['title'] == 'Add 2FA'
    assert len(states[1]['state']['issue']['title']) == 200
    assert len(states[1]['state']['issue']['body']) == 1500
    assert states[1]['state']['issue']['body_truncated']
    assert 'propos' in json.dumps(states[2]['questions']).lower()
    assert 'WSJF' in result['disclaimer']


def test_issue_ranking_uses_total_before_issue_number(monkeypatch):
    fake = IssueServices(assessments={1: jev_reply((1, 1, 1, 1)), 2: jev_reply((5, 3, 2, 1))})
    monkeypatch.setattr(pv, 'request_json', fake)
    result = asyncio.run(pv.assess_issue_value('o/r', [
        {'number': 1, 'title': 'Small convenience', 'body': ''},
        {'number': 2, 'title': 'Important outcome', 'body': ''}], 'gemini', jev_key='jev'))
    assert [(entry['number'], entry['cost_of_delay']) for entry in result['ranking']] == [(2, 11), (1, 4)]


@pytest.mark.parametrize('private,allow,token,permitted', [
    (True, False, 'caller', False), (True, True, None, False),
    (True, True, 'caller', True), (False, True, 'caller', True),
    (None, True, 'caller', False), (1, True, 'caller', False)])
def test_private_sources_require_explicit_mode_caller_and_bool_visibility(monkeypatch, private, allow, token, permitted):
    fake = FakeServices(private=private)
    monkeypatch.setattr(pv, 'request_json', fake)
    if not permitted:
        with pytest.raises(ValueError):
            asyncio.run(pv.fetch_product_sources('o/r', token, allow_private=allow))
        assert fake.readme_headers is None
    else:
        sources = asyncio.run(pv.fetch_product_sources('o/r', token, allow_private=allow))
        assert sources['private'] is private
        assert fake.readme_headers.get('Authorization') == ('Bearer caller' if private else None)


def test_owner_evidence_is_citable_bounded_and_never_fetched(monkeypatch):
    context = json.loads(json.dumps(CONTEXT))
    context['repository_research']['value_drivers'][0]['source_ids'] = ['owner-strategy']
    fake = IssueServices(context=context, private=True)
    monkeypatch.setattr(pv, 'request_json', fake)
    result = asyncio.run(pv.assess_issue_value('o/r', [], 'gemini', 'caller', jev_key='jev',
        allow_private=True, owner_context=[{'source_id': 'strategy', 'text': 'x' * 5000, 'url': 'https://private.invalid/strategy'}]))
    assert result['status'] == 'ok'
    assert result['ranking'] == []
    assert 'owner-strategy' in json.dumps(fake.context_schemas)
    source = next(s for s in result['sources']['research'] if s['kind'] == 'owner_context')
    assert source['truncated']
    assert not any('private.invalid' in url for url in fake.urls)


@pytest.mark.parametrize('reference,expected', [('owner-strategy', 'ok'), ('owner-invented', 'failed')])
def test_explicit_owner_profile_avoids_generation_and_validates_provenance(monkeypatch, reference, expected):
    profile = json.loads(json.dumps(CONTEXT))
    for claims in profile['repository_research'].values():
        for claim in claims:
            claim['source_ids'] = [reference]
    calls = []
    async def service(url, **kwargs):
        calls.append(url)
        assert 'typesafe.ai' in url, 'Owner profile must not silently trigger repository or Gemini research'
        return 200, {}, jev_reply()
    monkeypatch.setattr(pv, 'request_json', service)
    result = asyncio.run(pv.assess_issue_value('o/r', [{'number': 1, 'title': 'Restore inquiries', 'body': ''}],
        None, jev_key='jev', owner_context=[{'source_id': 'strategy', 'text': 'Qualified inquiries.'}], owner_profile=profile))
    assert result['status'] == expected
    assert len(calls) == (1 if expected == 'ok' else 0)
    if expected == 'ok':
        assert result['context_method'] == 'owner_profile' and result['context_model'] is None
