import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prompt_studio_core import StudioDB


class ResultQueueScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        sys.modules.setdefault('gradio', types.ModuleType('gradio'))
        import prompt_studio_ui as ui
        self.ui = ui
        self.db = StudioDB(Path(self.temp.name) / 'results.db')
        self.queue_db = StudioDB(Path(self.temp.name) / 'queue.db')
        queue_patcher = mock.patch.object(ui, 'DB', self.queue_db)
        queue_patcher.start()
        self.addCleanup(queue_patcher.stop)
        patcher = mock.patch.object(ui, 'RESULT_DB', self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        gallery = mock.patch.object(ui, '_server_queue_gallery', return_value=[])
        gallery.start()
        self.addCleanup(gallery.stop)

    def test_empty_selection_does_not_append_or_enqueue_all(self):
        self.db.save_prompt('cached prompt')
        with mock.patch.object(self.ui, '_enqueue_server_queue') as enqueue:
            prompt, message = self.ui._processed_result_append([])
            queues, status = self.ui._processed_result_enqueue([], queue_ids=['previous'])
        self.assertEqual(prompt, '')
        self.assertIn('选择', message)
        self.assertIn('选择', status)
        self.assertEqual(queues, ['previous'])
        enqueue.assert_not_called()

    def test_filtered_scope_is_explicit_and_preserves_existing_queues(self):
        self.db.save_prompt('cat one')
        self.db.save_prompt('cat two')
        self.db.save_prompt('dog')
        prompts, _ = self.ui._processed_result_append([], 'cat', scope='filtered')
        self.assertEqual(set(prompts.splitlines()), {'cat one', 'cat two'})
        with mock.patch.object(self.ui, '_enqueue_server_queue', return_value={'batch_id': 'new'}) as enqueue:
            queues, _ = self.ui._processed_result_enqueue([], 'cat', ['previous'], scope='filtered')
        self.assertEqual(queues, ['previous', 'new'])
        self.assertEqual(set(enqueue.call_args.args[0]['requests']), {'cat one', 'cat two'})
        self.assertTrue(enqueue.call_args.args[0]['config']['direct_prompt'])

    def test_partial_submission_records_success_before_retry(self):
        stage = {'items': [{'record': {'id': i}, 'result': f'prompt {i}'} for i in range(1001)]}
        with mock.patch.object(self.ui, '_enqueue_server_queue', side_effect=[{'batch_id': 'first'}, ValueError('fail')]):
            updated, queues, message, _ = self.ui._inline_cache_enqueue_results(stage, queue_ids=['previous'])
        self.assertIn('失败', message)
        self.assertEqual(len(updated['queued_ids']), 1000)
        self.assertEqual(queues, ['previous', 'first'])
        self.assertEqual(updated['queue_ids'], queues)
        with mock.patch.object(self.ui, '_enqueue_server_queue', return_value={'batch_id': 'second'}) as enqueue:
            final, queues, _, _ = self.ui._inline_cache_enqueue_results(updated, queue_ids=queues)
        self.assertEqual(enqueue.call_args.args[0]['requests'], ['prompt 1000'])
        self.assertEqual(queues, ['previous', 'first', 'second'])
        self.assertEqual(len(final['queued_ids']), 1001)
        with mock.patch.object(self.ui, '_enqueue_server_queue') as enqueue:
            _, repeated, _, _ = self.ui._inline_cache_enqueue_results(final, queue_ids=queues)
        self.assertEqual(repeated, queues)
        enqueue.assert_not_called()

    def test_empty_stage_retains_shared_queues(self):
        _, queues, _, _ = self.ui._inline_cache_enqueue_results({}, queue_ids=['previous'])
        self.assertEqual(queues, ['previous'])

    def test_library_partial_submission_retry_only_submits_remaining_record(self):
        self.db.save_prompts_batch([{'prompt': f'prompt {i}'} for i in range(1001)])
        real_enqueue = self.ui._enqueue_server_queue
        calls = []

        def fail_second(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise ValueError('second chunk failed')
            return real_enqueue(payload)

        with mock.patch.object(self.ui, '_ensure_server_queue_worker'), mock.patch.object(self.ui._SERVER_QUEUE_WAKE, 'set'):
            with mock.patch.object(self.ui, '_enqueue_server_queue', side_effect=fail_second):
                queues, status = self.ui._processed_result_enqueue([], scope='filtered')
            self.assertIn('失败', status)
            self.assertEqual(len(queues), 1)
            with mock.patch.object(self.ui, '_enqueue_server_queue', wraps=real_enqueue) as enqueue:
                queues, _ = self.ui._processed_result_enqueue([], queue_ids=queues, scope='filtered')
            self.assertEqual(len(enqueue.call_args.args[0]['requests']), 1)
            self.assertEqual(len(queues), 2)
            with mock.patch.object(self.ui, '_enqueue_server_queue') as enqueue:
                repeated, _ = self.ui._processed_result_enqueue([], queue_ids=queues, scope='filtered')
            self.assertEqual(repeated, queues)
            enqueue.assert_not_called()

    def test_library_dedupe_counts_matching_jobs_and_allows_failed_or_different_settings(self):
        records = [{'id': i, 'prompt': 'same prompt'} for i in range(7)]
        config = {'source': 'processed_result_db', 'generation_settings': {'width': 512}}
        jobs = [
            {'id': str(i), 'request': 'same prompt', 'config': config}
            for i in range(5)
        ] + [
            {'id': 'stage', 'request': 'same prompt', 'config': {'generation_settings': {'width': 512}}},
            {'id': 'different', 'request': 'same prompt', 'config': {**config, 'generation_settings': {'width': 768}}},
        ]
        self.queue_db.enqueue_server_queue('existing', jobs)
        for ident, status in [('1', 'running'), ('2', 'completed'), ('3', 'error'), ('4', 'cancelled')]:
            self.queue_db.update_server_queue_job(ident, status)
        with mock.patch.object(self.ui, '_processed_result_records', return_value=records), mock.patch.object(
            self.ui, '_enqueue_server_queue', return_value={'batch_id': 'new'}
        ) as enqueue:
            queues, _ = self.ui._processed_result_enqueue([], queue_ids=['existing'], generation_settings={'width': 512}, scope='filtered')
        self.assertEqual(queues, ['existing', 'new'])
        self.assertEqual(enqueue.call_args.args[0]['requests'], ['same prompt'] * 4)

    def test_empty_prompts_are_not_marked_as_queued(self):
        stage = {'items': [{'record': {'id': 1}, 'result': ''}, {'record': {'id': 2}, 'result': 'valid'}]}
        with mock.patch.object(self.ui, '_enqueue_server_queue', return_value={'batch_id': 'new'}):
            updated, _, _, _ = self.ui._inline_cache_enqueue_results(stage)
        self.assertEqual(updated['queued_ids'], ['2'])


if __name__ == '__main__':
    unittest.main()
