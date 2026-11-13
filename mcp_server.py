"""Stateless Streamable HTTP MCP tools and the inline dashboard resource."""
import base64
import json
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from github_auth import identify_token
from mcp_oauth import SCOPE, access_grant, bearer_challenge, bounded_body

URI = 'ui://github-analytics/dashboard-v1.html'
MIME = 'text/html;profile=mcp-app'
RESOURCE_META = {
    'ui': {'prefersBorder': True, 'csp': {'connectDomains': [], 'resourceDomains': ['https://cdn.jsdelivr.net']}},
    'openai/widgetCSP': {'connect_domains': [], 'resource_domains': ['https://cdn.jsdelivr.net']},
    'openai/ui': {'availableDisplayModes': ['inline', 'fullscreen'], 'preferredDisplayMode': 'inline'},
    'openai/widgetDescription': 'Interactive GitHub flow charts, backlog, content framing, cost of delay and coaching.',
}
RESOURCE = {'uri': URI, 'name': 'GitHub Flow Analytics dashboard', 'mimeType': MIME, '_meta': RESOURCE_META}
SERVER = {'name': 'github-flow-analytics', 'version': '1.0.0'}
VERSIONS = ['2026-07-28', '2025-11-25', '2025-06-18', '2025-03-26']
CAPABILITIES = {'tools': {}, 'resources': {}, 'extensions': {'io.modelcontextprotocol/ui': {'mimeTypes': [MIME]}}}
INSTRUCTIONS = (
    'Use show_github_dashboard when the user asks for repository flow or an interactive chart. '
    'For issue cost of delay, use show_issue_value. Assessments describe stated intent and expected value. '
    'Ask for explicit permission to share private analytics with the chat client and process repository '
    'context with Gemini and Jev before passing allow_private. Never ask for access tokens.'
)


class DashboardArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repo: str | None = Field(default=None, max_length=200, description='GitHub owner/repo or github.com URL.')
    view: Literal['overview', 'flow', 'work', 'batch', 'issues', 'value'] = 'overview'
    force_refresh: bool = Field(default=False, strict=True)
    allow_private: bool = Field(default=False, strict=True, description='Enable only with explicit permission to process private data.')


class RefreshArgs(DashboardArgs):
    repo: str = Field(min_length=1, max_length=200)


class IssueArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repo: str = Field(max_length=200)
    allow_private: bool = Field(default=False, strict=True)
    max_issues: int = Field(default=20, ge=1, le=50, strict=True)
    issue_numbers: list[Annotated[int, Field(strict=True, ge=1)]] | None = Field(default=None, min_length=1, max_length=50)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(max_length=4000)


class CoachArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repo: str = Field(max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    analysis_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    allow_private: bool = Field(default=False, strict=True)
    history: list[Message] = Field(default_factory=list, max_length=10)


def definition(name, description, model, *, ui=True, app_only=False):
    security = [{'type': 'oauth2', 'scopes': [SCOPE]}]
    meta = {'securitySchemes': security, 'openai/widgetAccessible': True}
    if ui:
        meta.update({'ui': {'resourceUri': URI}, 'openai/outputTemplate': URI})
    if app_only:
        meta['ui'] = {'visibility': ['app']}
        meta['openai/visibility'] = 'private'
    schema = model.model_json_schema()
    schema.pop('title', None)
    return {'name': name, 'description': description, 'inputSchema': schema, 'securitySchemes': security,
            'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': True}, '_meta': meta}


TOOLS = [
    definition('show_github_dashboard',
        'Open the interactive repository dashboard. Includes KPIs, cumulative flow, cycle time and SLEs, '
        'throughput, active WIP, PR size and timeline, backlog health, framing and PR cost of delay. '
        'Choose a starting view, or omit repo to let the user enter one.', DashboardArgs),
    definition('show_issue_value',
        'Assess expected cost of delay for GitHub issues, optionally selecting exact issue numbers. '
        'Use the results to prioritize proposed issue work, including repos without merged PRs.', IssueArgs),
    definition('ask_flow_coach',
        'Ask the flow coach about a dashboard snapshot. Pass the repo and analysis_id from the dashboard. For private snapshots, use allow_private only with consent.',
        CoachArgs, ui=False),
    definition('refresh_github_dashboard', 'Analyze or refresh the repository in the dashboard.',
        RefreshArgs, ui=False, app_only=True),
    definition('assess_dashboard_issues', 'Assess the selected issues in the dashboard.',
        IssueArgs, ui=False, app_only=True),
]


async def asset(request, path):
    assets = getattr(request.scope.get('env'), 'ASSETS', None)
    if assets is None:
        raise HTTPException(503, 'Dashboard assets are unavailable.')
    response = await assets.fetch(f'https://assets.local/{path}')
    if response.status != 200:
        raise HTTPException(503, 'Dashboard assets are unavailable.')
    return (await response.bytes()).decode('utf-8')


def tool_result(text, data, **view):
    result = {'content': [{'type': 'text', 'text': text}], 'structuredContent': data}
    if view:
        result['_meta'] = view
    return result


def summary(metrics, view):
    stats = metrics.get('recent_stats', {})
    merged = stats.get('total', 0)
    tp = metrics.get('throughput_data', [])[1:-1][-8:]
    data = {'repo': metrics['repo'], 'view': view, 'visibility': metrics.get('repository_visibility', 'public'),
            'analysis_id': metrics.get('analysis_id'), 'captured_at': metrics.get('captured_at'),
            'merged_pr_sample': merged, 'p50_cycle_hours': metrics.get('p50_sle') if merged else None,
            'p85_cycle_hours': metrics.get('p85_sle') if merged else None,
            'active_prs': len(metrics.get('active_wip_points', [])),
            'recent_complete_weeks': [{'week': row['week'], 'merged_prs': row['total']} for row in tp]}
    if issues := metrics.get('issues'):
        data.update({'open_issues_in_sample': issues['open_now'], 'open_issues_capped': issues['open_truncated'],
                     'net_backlog_8w': issues['net_8w'], 'stale_issues_in_sample': issues['stale']['count']})
    return data


def routing_name(header):
    if header.startswith('=?base64?') and header.endswith('?='):
        try:
            return base64.b64decode(header[len('=?base64?'):-2], validate=True).decode('utf-8')
        except ValueError:
            return None
    return header


def install_mcp(app, analyze, issue_value, coach):
    async def call_tool(params, request, grant):
        name = params.get('name')
        args = params.get('arguments', {})
        if name in ('show_github_dashboard', 'refresh_github_dashboard'):
            schema = DashboardArgs if name == 'show_github_dashboard' else RefreshArgs
            inputs = schema.model_validate(args)
            if inputs.repo is None and name == 'show_github_dashboard':
                return tool_result('Enter a GitHub repository in the dashboard.', {'view': inputs.view},
                                   privateEnabled=bool(grant['private_consent']))
        elif name in ('show_issue_value', 'assess_dashboard_issues'):
            inputs = IssueArgs.model_validate(args)
        elif name == 'ask_flow_coach':
            inputs = CoachArgs.model_validate(args)
        else:
            raise HTTPException(400, 'Unknown GitHub Analytics tool.')

        if inputs.allow_private and not grant['private_consent']:
            raise HTTPException(403, 'Reconnect GitHub Flow Analytics and enable private repositories during sign-in.')
        identity = await identify_token(grant['github_token'])
        if str(identity.github_id) != str(grant['github_id']):
            raise HTTPException(401, 'The GitHub identity changed. Reconnect GitHub Flow Analytics.')
        if isinstance(inputs, CoachArgs):
            answer = await coach(inputs, request, identity, full_dashboard=True)
            return tool_result(answer['reply'], answer)
        if isinstance(inputs, IssueArgs):
            results = await issue_value(IssueAnalysisInput(**inputs.model_dump(), repo_url=inputs.repo), request, identity, mcp=True)
            value = results['product_value']
            data = {'repo': results['repo'], 'view': 'issue-value', 'visibility': results['repository_visibility'],
                    'status': value['status'], 'assessed': value.get('assessed', 0), 'ranking': value.get('ranking', [])}
            return tool_result(f'Issue cost-of-delay assessment for {results["repo"]}.', data,
                               issueValue=results, privateEnabled=bool(grant['private_consent']))

        metrics = await analyze(AnalysisInput(repo_url=inputs.repo or '', force_refresh=inputs.force_refresh,
                              allow_private=inputs.allow_private), request, identity, full_dashboard=True)
        data = summary(metrics, inputs.view)
        text = (f'{data["visibility"].capitalize()} repository {data["repo"]}. '
                f'{data["merged_pr_sample"]} merged PRs and {data["active_prs"]} open PRs in the sample.')
        if data['p85_cycle_hours'] is not None:
            text += f' P85 cycle time {data["p85_cycle_hours"]:.1f} hours.'
        if 'open_issues_in_sample' in data:
            text += f' {data["open_issues_in_sample"]} open issues in the sample.'
        return tool_result(text, data, metrics=metrics, privateEnabled=bool(grant['private_consent']))

    @app.api_route('/mcp', methods=['GET', 'DELETE'])
    async def no_stream(request: Request):
        _, origin = await access_grant(request)
        if request.headers.get('origin') not in (None, origin):
            raise HTTPException(403, 'MCP request origin is not allowed.')
        return Response(status_code=405, headers={'Allow': 'POST'})

    @app.post('/mcp')
    async def mcp(request: Request):
        grant, origin = await access_grant(request)
        if request.headers.get('origin') not in (None, origin):
            raise HTTPException(403, 'MCP request origin is not allowed.')
        accept = request.headers.get('accept', '')
        if 'application/json' not in accept or 'text/event-stream' not in accept:
            raise HTTPException(406, 'Accept application/json and text/event-stream.')
        if request.headers.get('content-type', '').partition(';')[0].strip() != 'application/json':
            raise HTTPException(415, 'Use application/json.')

        def error(code, message, request_id=None, status=200, data=None):
            detail = {'code': code, 'message': message}
            if data is not None:
                detail['data'] = data
            return JSONResponse({'jsonrpc': '2.0', 'id': request_id, 'error': detail}, status_code=status)

        try:
            body = json.loads(await bounded_body(request))
        except (ValueError, RecursionError):
            return error(-32700, 'Invalid JSON.', status=400)
        if not isinstance(body, dict) or body.get('jsonrpc') != '2.0' or not isinstance(body.get('method'), str):
            return error(-32600, 'Invalid JSON-RPC request.', status=400)
        method, request_id, params = body['method'], body.get('id'), body.get('params', {})
        if not isinstance(params, dict):
            return error(-32602, 'Parameters must be an object.', request_id, 400)
        if 'id' not in body:
            return Response(status_code=202)
        if type(request_id) not in (str, int):
            return error(-32600, 'Invalid JSON-RPC request ID.', status=400)
        version = request.headers.get('mcp-protocol-version')
        metadata = params.get('_meta', {})
        if not isinstance(metadata, dict):
            return error(-32602, 'Invalid request metadata.', request_id, 400)
        declared_version = metadata.get('io.modelcontextprotocol/protocolVersion')
        if version and version not in VERSIONS:
            return error(-32022, 'Unsupported protocol version.', request_id, 400,
                         {'supported': VERSIONS, 'requested': version})
        modern = version == VERSIONS[0]
        if modern or declared_version is not None:
            field = {'tools/call': 'name', 'resources/read': 'uri', 'prompts/get': 'name'}.get(method)
            if (declared_version != version or request.headers.get('mcp-method') != method
                    or (field and routing_name(request.headers.get('mcp-name', '')) != params.get(field))):
                return error(-32020, 'MCP routing headers do not match the request.', request_id, 400)
            if method == 'initialize':
                return error(-32601, 'Method not found.', request_id, 404)
        if method == 'server/discover':
            result = {'supportedVersions': VERSIONS, 'capabilities': CAPABILITIES, 'instructions': INSTRUCTIONS,
                      '_meta': {'io.modelcontextprotocol/serverInfo': SERVER}}
        elif method == 'initialize':
            protocol = params.get('protocolVersion')
            result = {'protocolVersion': protocol if protocol in VERSIONS[1:] else VERSIONS[1],
                      'serverInfo': SERVER, 'capabilities': CAPABILITIES, 'instructions': INSTRUCTIONS}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'resources/list':
            result = {'resources': [RESOURCE]}
        elif method == 'resources/templates/list':
            result = {'resourceTemplates': []}
        elif method == 'resources/read':
            if params.get('uri') != URI:
                return error(-32002, 'Resource not found.', request_id)
            result = {'contents': [{**RESOURCE, 'text': await asset(request, 'mcp-dashboard')}]}
        elif method == 'tools/call':
            try:
                result = await call_tool(params, request, grant)
            except ValidationError:
                result = {'isError': True, 'content': [{'type': 'text', 'text': 'Check the tool arguments.'}]}
            except HTTPException as exc:
                result = {'isError': True, 'content': [{'type': 'text', 'text': exc.detail}]}
                if exc.status_code == 401:
                    result['_meta'] = {'mcp/www_authenticate': [bearer_challenge(origin, invalid=True)]}
        else:
            return error(-32601, 'Method not found.', request_id, 404 if modern else 200)
        if modern:
            result.setdefault('_meta', {}).update({'io.modelcontextprotocol/serverInfo': SERVER})
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


class AnalysisInput(BaseModel):
    repo_url: str
    allow_private: bool
    force_refresh: bool


class IssueAnalysisInput(IssueArgs):
    repo_url: str
    owner_context: list = Field(default_factory=list)
    owner_profile: dict | None = None
