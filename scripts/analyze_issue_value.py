"""Analyze private issues through the API using the active gh identity.

Keys come from environment only. Private evidence is written only to --output.
The default invokes the actual hosted ASGI endpoint locally with live providers.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo')
    parser.add_argument('--owner-context', type=Path)
    parser.add_argument('--owner-profile', type=Path, help='Explicit validated profile; skips Gemini generation for this run.')
    parser.add_argument('--issue-sample', type=Path, help='JSON with a numbers array; selection is rechecked live.')
    parser.add_argument('--limit', type=int, default=20, choices=range(1, 51))
    parser.add_argument('--output', type=Path, required=True, help='Private local directory; never a public repository path.')
    return parser.parse_args()


async def analyze(args):
    from local_private_api import create_application
    import product_value
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Private evidence must be outside the application repository.')
    token = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'], capture_output=True, text=True, check=True).stdout.strip()
    request = {'repo_url': args.repo, 'allow_private': True, 'max_issues': args.limit}
    if args.owner_context:
        evidence = json.loads(args.owner_context.read_text())
        sources = evidence.get('sources', []) if isinstance(evidence, dict) else evidence
        request['owner_context'] = [{key: value for key, value in {
            'source_id': source.get('source_id') or source['id'].removeprefix('owner-'),
            'text': source['text'], 'path': source.get('path'), 'url': source.get('url')}.items() if value is not None}
            for source in sources]
    if args.issue_sample:
        request['issue_numbers'] = json.loads(args.issue_sample.read_text())['numbers']
    if args.owner_profile:
        request['owner_profile'] = json.loads(args.owner_profile.read_text())
    if args.output.exists():
        raise ValueError('Use a fresh output directory; existing evidence will not be overwritten.')
    args.output.mkdir(parents=True)
    (args.output / 'request.json').write_text(json.dumps(request, indent=2))
    provider_calls = []
    transport_request = product_value._request
    async def observed_request(url, timeout, **kwargs):
        provider = 'gemini' if url == product_value.GEMINI_URL else 'jev' if url == product_value.JEV_URL else None
        if not provider:
            return await transport_request(url, timeout, **kwargs)
        maximum = 3 if provider == 'gemini' else args.limit
        if sum(call['provider'] == provider for call in provider_calls) >= maximum:
            raise product_value.GenerationError('assessment_deadline')
        record = {'provider': provider, 'request': kwargs.get('body'), 'outcome': 'started'}
        provider_calls.append(record)
        def checkpoint():
            (args.output / 'provider-calls.json').write_text(json.dumps(provider_calls, indent=2))
        checkpoint()
        try:
            result = await transport_request(url, timeout, **kwargs)
            record.update(outcome='completed', http_status=result[0], response=result[2])
            return result
        except BaseException as exc:
            record.update(outcome='failed', error_type=type(exc).__name__)
            raise
        finally:
            checkpoint()
    product_value._request = observed_request
    transport = httpx.ASGITransport(app=create_application())
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost', timeout=180) as client:
            response = await client.post('/api/issues/value', headers={'Authorization': f'Bearer {token}'}, json=request)
    finally:
        product_value._request = transport_request
    (args.output / 'response.json').write_text(json.dumps(response.json(), indent=2))
    if response.status_code != 200:
        raise ValueError(f'Analysis endpoint returned HTTP {response.status_code}; inspect private response.json.')
    data = response.json()
    value = data['product_value']
    summary = {'status': value['status'], 'assessed': value.get('assessed', 0), 'failed': value.get('failed', 0),
        'selected': value.get('selected', 0), 'available': data['selection']['total_count'],
        'ranking': value.get('ranking', []), 'output': str(args.output)}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if value['status'] != 'ok':
        raise ValueError('Value context was not available; see private evidence.')


if __name__ == '__main__':
    try:
        asyncio.run(analyze(arguments()))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, httpx.HTTPError) as exc:
        # Subprocess errors may contain command output; never print them.
        print(f'Analysis failed ({type(exc).__name__}); no credentials were printed.', file=sys.stderr)
        sys.exit(1)
