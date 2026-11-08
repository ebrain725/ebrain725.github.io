"""Orphan cleanup must never cancel successful or live-owner deployments."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pages_deployment_guard import GitHub, GuardError, wait_for_slot

REPO = 'ebrain725/ebrain725.github.io'
PREFIX = '/repos/'+REPO
SHA = 'a'*40


def run(run_id=1, **extra):
    return dict(id=run_id, path='.github/workflows/sync-policies.yml',
                head_branch='main', head_sha=SHA,
                status=extra.get('status', 'completed'),
                conclusion=extra.get('conclusion', 'failure'))


class PagesGuardTests(unittest.TestCase):
    def execute(self, api, budget=120):
        return wait_for_slot(api, REPO, 99, budget_seconds=budget, sleep=lambda seconds: None)

    def test_completed_successful_deployment_is_never_cancelled(self):
        api = Mock(side_effect=[dict(workflow_runs=[run()]), dict(status='succeed')])
        self.assertEqual(self.execute(api), 0)
        self.assertFalse(any(call.args[0] == 'POST' for call in api.call_args_list))

    def test_official_terminal_error_states_do_not_block_a_later_deployment(self):
        for state in ('deployment_content_failed', 'deployment_lost', 'deployment_failed', 'deployment_cancelled'):
            api = Mock(side_effect=[dict(workflow_runs=[run()]), dict(status=state)])
            self.assertEqual(self.execute(api), 0)
            self.assertFalse(any(call.args[0] == 'POST' for call in api.call_args_list))

    def test_only_rechecked_completed_failure_is_cancelled(self):
        api = Mock(side_effect=[dict(workflow_runs=[run()]), dict(status='deployment_in_progress'),
                               run(), {}, dict(status='cancelled')])
        self.assertEqual(self.execute(api), 1)
        self.assertEqual(api.call_args_list[3].args,
                         ('POST', PREFIX+'/pages/deployments/'+SHA+'/cancel'))

    def test_same_sha_owned_by_another_live_run_is_not_cancelled(self):
        api = Mock(side_effect=[dict(workflow_runs=[run(), run(2, status='in_progress')]),
                               dict(status='deployment_in_progress')])
        with self.assertRaisesRegex(GuardError, 'NOT_RELEASED'):
            self.execute(api, budget=0)
        self.assertFalse(any(call.args[0] == 'POST' for call in api.call_args_list))

    def test_owner_rerun_between_list_and_cancel_is_not_cancelled(self):
        api = Mock(side_effect=[dict(workflow_runs=[run()]), dict(status='queued'),
                               run(status='in_progress'), dict(status='succeed')])
        self.assertEqual(self.execute(api), 0)
        self.assertFalse(any(call.args[0] == 'POST' for call in api.call_args_list))

    def test_unknown_state_fails_closed(self):
        api = Mock(side_effect=[dict(workflow_runs=[run()]), dict(status='new_unknown_state')])
        with self.assertRaisesRegex(GuardError, 'UNKNOWN'):
            self.execute(api)

    def test_missing_pages_deployment_is_not_an_error(self):
        api = Mock(side_effect=[dict(workflow_runs=[run()]), None])
        self.assertEqual(self.execute(api), 0)

    def test_does_not_inspect_non_pages_or_current_run(self):
        other = dict(run(), path='.github/workflows/sync-ets-energy.yml')
        api = Mock(return_value=dict(workflow_runs=[other, run(99)]))
        self.assertEqual(self.execute(api), 0)
        self.assertEqual(api.call_count, 1)

    def test_write_api_is_restricted_to_exact_pages_cancellation(self):
        api = GitHub(REPO, 'synthetic-fixture-token')
        for path in (PREFIX+'/actions/runs/1/cancel', '/repos/other/repo/pages/deployments/'+SHA+'/cancel'):
            with self.assertRaises(GuardError):
                api('POST', path)


if __name__ == '__main__':
    unittest.main()
