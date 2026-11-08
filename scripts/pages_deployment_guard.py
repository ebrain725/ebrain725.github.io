#!/usr/bin/env python3
"""Clear orphaned Pages deployments without cancelling a live workflow.

The workflows already share one concurrency group. A deployment can nevertheless
outlive its failed/timed-out workflow and block the next creation request. Only
an exact SHA belonging to a rechecked, completed failed workflow is cancellable.
Successful deployments and any SHA owned by another live run are never cancelled.
The normal deploy action and public-file verification still determine success.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request


WORKFLOWS = frozenset({
    '.github/workflows/deploy-pages.yml',
    '.github/workflows/sync-policies.yml',
    '.github/workflows/sync-market.yml',
    '.github/workflows/sync-krx-daily-trends.yml',
    '.github/workflows/merge-official-policy-history.yml',
    '.github/workflows/merge-motie-policy-history.yml',
    '.github/workflows/merge-policy-event-expansion.yml',
    '.github/workflows/apply-policy-event-taxonomy.yml',
    '.github/workflows/apply-policy-radar-38-topics.yml',
    '.github/workflows/export-policy-radar-excel-compatible.yml',
})
TERMINAL = frozenset({'succeed', 'succeeded', 'success', 'failed', 'failure',
                      'canceled', 'cancelled', 'deployment_failed', 'deployment_cancelled'})
ACTIVE = frozenset({'', 'queued', 'pending', 'building', 'build_in_progress',
                    'deploying', 'deployment_in_progress', 'in_progress',
                    'deployment_queued', 'deployment_pending'})
FAILED = frozenset({'failure', 'cancelled', 'timed_out', 'action_required'})


class GuardError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repository, token):
        if repository != 'ebrain725/ebrain725.github.io' or not token:
            raise GuardError('PAGES_GUARD_REPOSITORY_OR_TOKEN_INVALID')
        self.prefix = '/repos/'+repository
        self.token = token
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, method, path, allow_missing=False):
        if method not in {'GET', 'POST'} or not path.startswith(self.prefix+'/'):
            raise GuardError('PAGES_GUARD_ENDPOINT_INVALID')
        if method == 'POST' and not re.fullmatch(
                re.escape(self.prefix)+r'/pages/deployments/[0-9a-f]{40}/cancel', path):
            raise GuardError('PAGES_GUARD_WRITE_ENDPOINT_INVALID')
        request = urllib.request.Request('https://api.github.com'+path,
            method=method, data=b'' if method == 'POST' else None,
            headers={'Authorization': 'Bearer '+self.token,
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'ETS-Pages-Deployment-Guard'})
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read(4_000_001)
        except urllib.error.HTTPError as error:
            if error.code == 404 and allow_missing:
                return None
            raise GuardError('PAGES_GUARD_API_HTTP_'+str(error.code)) from None
        except (OSError, urllib.error.URLError):
            raise GuardError('PAGES_GUARD_API_TRANSPORT_ERROR') from None
        if len(body) > 4_000_000:
            raise GuardError('PAGES_GUARD_RESPONSE_TOO_LARGE')
        try:
            return json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            raise GuardError('PAGES_GUARD_RESPONSE_INVALID') from None


def wait_for_slot(api, repository, current_run, budget_seconds=120,
                  clock=time.monotonic, sleep=time.sleep):
    prefix = '/repos/'+repository
    deadline = clock()+budget_seconds
    document = api('GET', prefix+'/actions/runs?branch=main&per_page=100')
    runs = document.get('workflow_runs')
    if not isinstance(runs, list):
        raise GuardError('PAGES_GUARD_RUN_LIST_INVALID')
    relevant = [run for run in runs if run.get('path') in WORKFLOWS
                and run.get('head_branch') == 'main'
                and str(run.get('id')) != str(current_run)]
    protected = {run.get('head_sha') for run in relevant if run.get('status') != 'completed'}
    candidates = {}
    for run in relevant:
        sha = run.get('head_sha', '')
        if (run.get('status') == 'completed' and run.get('conclusion') in FAILED
                and re.fullmatch(r'[0-9a-f]{40}', sha)):
            candidates.setdefault(sha, run)
    cancelled = 0
    for sha, owner in list(candidates.items())[:10]:
        endpoint = prefix+'/pages/deployments/'+sha
        status = api('GET', endpoint, allow_missing=True)
        if status is None or status.get('status') in TERMINAL:
            continue
        state = status.get('status')
        if state not in ACTIVE:
            raise GuardError('PAGES_GUARD_UNKNOWN_DEPLOYMENT_STATE')
        if sha not in protected:
            # Recheck ownership immediately before this narrowly scoped write.
            latest_owner = api('GET', prefix+'/actions/runs/'+str(owner['id']))
            if (latest_owner.get('status') == 'completed'
                    and latest_owner.get('conclusion') in FAILED
                    and latest_owner.get('head_sha') == sha):
                api('POST', endpoint+'/cancel', allow_missing=True)
                cancelled += 1
                print('PAGES_ORPHAN_CANCEL_REQUESTED run='+str(owner['id'])+' sha='+sha, flush=True)
        while True:
            if clock() >= deadline:
                raise GuardError('PAGES_PREVIOUS_DEPLOYMENT_NOT_RELEASED')
            status = api('GET', endpoint, allow_missing=True)
            if status is None or status.get('status') in TERMINAL:
                break
            if status.get('status') not in ACTIVE:
                raise GuardError('PAGES_GUARD_UNKNOWN_DEPLOYMENT_STATE')
            sleep(min(5, max(0, deadline-clock())))
    print('PAGES_DEPLOYMENT_GUARD_OK checked='+str(min(10, len(candidates)))+
          ' orphan_cancel_requests='+str(cancelled), flush=True)
    return cancelled


def main():
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    api = GitHub(repository, os.environ.get('GH_TOKEN', ''))
    wait_for_slot(api, repository, os.environ['GITHUB_RUN_ID'])


if __name__ == '__main__':
    try:
        main()
    except GuardError as error:
        raise SystemExit(str(error)) from None
