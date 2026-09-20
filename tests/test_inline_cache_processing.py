import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prompt_studio_core import StudioDB


class InlineCacheProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = StudioDB(Path(self.temp.name) / 'cache.db')
        sys.modules.setdefault('gradio', types.ModuleType('gradio'))
        import prompt_studio_ui as ui
        self.ui = ui
        patcher = mock.patch.object(ui, 'DB', self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.result_db = StudioDB(Path(self.temp.name) / 'processed.db')
        result_patcher = mock.patch.object(ui, 'RESULT_DB', self.result_db)
        result_patcher.start()
        self.addCleanup(result_patcher.stop)

    def run_batch(self, query='', scope='全部筛选结果', record=None, source=''):
        return list(self.ui._inline_cache_run(scope, record or {}, source, query, '全部', '全部',
                                             'Polish', 'Krea 2 Natural', 'Krea 2', '', 'test-cache'))[-1]

    def test_stages_all_filtered_without_writing_and_preserves_metadata_on_save(self):
        self.db.save_prompts_batch([{'prompt': f'cat {i}', 'tags': 'original tags'} for i in range(1005)])
        dog = self.db.save_prompt('dog')
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=lambda source, *a: (source + ' polished', 'ok')):
            stage, rows, result, status = self.run_batch('cat')
        self.assertEqual(len(stage['items']), 1005)
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(self.db.list_prompts('polished', limit=None)), 0)
        saved, message = self.ui._inline_cache_save(stage, '全部处理结果', 0, '')
        self.assertEqual(saved, {})
        self.assertIn('1005', message)
        self.assertEqual(len(self.db.list_prompts('polished', limit=None)), 1005)
        self.assertEqual(self.db.list_prompts('polished')[0]['tags'], 'original tags')
        self.assertEqual(self.db.get_prompt(dog)['prompt'], 'dog')

    def test_processed_results_are_saved_to_separate_db_without_touching_source_cache(self):
        source_id = self.db.save_prompt('source prompt')
        with mock.patch.object(self.ui, '_inline_cache_transform', return_value=('processed prompt', 'ok')):
            stage, *_ = self.run_batch()
        self.assertEqual(self.db.get_prompt(source_id)['prompt'], 'source prompt')
        processed = self.result_db.list_prompts(limit=None)
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]['prompt'], 'processed prompt')
        self.assertEqual(processed[0]['source_kind'], 'inline_processed')
        self.assertEqual(stage['items'][0]['result_db_id'], processed[0]['id'])

    def test_processed_result_library_can_enqueue_selected_records_directly(self):
        self.result_db.save_prompt('processed one', source_kind='inline_processed', source_ref='test:1')
        self.result_db.save_prompt('processed two', source_kind='inline_processed', source_ref='test:2')
        with mock.patch.object(self.ui, '_ensure_server_queue_worker'), mock.patch.object(self.ui._SERVER_QUEUE_WAKE, 'set'):
            batch_ids, message = self.ui._processed_result_enqueue(['1'], '', [])
        self.assertEqual(len(batch_ids), 1)
        jobs = self.db.list_server_queue(batch_ids[0], 10)
        self.assertEqual([job['request'] for job in jobs], ['processed one'])
        self.assertEqual(jobs[0]['target'], 'txt2img')
        self.assertTrue(jobs[0]['config']['direct_prompt'])
        self.assertIn('1 条', message)

    def test_stale_record_rolls_back_entire_save(self):
        first = self.db.save_prompt('cat one')
        second = self.db.save_prompt('cat two')
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=lambda source, *a: (source + '!', 'ok')):
            stage, *_ = self.run_batch()
        self.db.edit_prompts([self.db.get_prompt(second)], 'prompt', 'set', '', 'newer')
        retained, message = self.ui._inline_cache_save(stage, '全部处理结果', 0, '')
        self.assertEqual(retained, stage)
        self.assertIn('已变化', message)
        self.assertEqual(self.db.get_prompt(first)['prompt'], 'cat one')

    def test_failure_and_cancel_report_counts_and_can_resume_unfinished_records(self):
        self.db.save_prompt('cat one')
        self.db.save_prompt('cat two')
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=[('good', 'ok'), ('', 'failed')]):
            stage, _, _, status = self.run_batch()
        self.assertFalse(stage['complete'])
        self.assertIn('已处理 1', status)
        self.assertIn('未处理 1', status)
        self.assertIn('失败 1', status)
        self.assertIn('未完整完成', self.ui._inline_cache_save(stage, '全部处理结果', 0, '')[1])

        with mock.patch.object(self.ui, '_inline_cache_transform', return_value=('recovered', 'ok')):
            resumed, _, _, resumed_status = list(self.ui._inline_cache_resume(stage, 'test-resume'))[-1]
        self.assertTrue(resumed['complete'])
        self.assertEqual(len(resumed['items']), 2)
        self.assertIn('已处理 2', resumed_status)
        self.assertIn('未处理 0', resumed_status)

        def cancel(source, action, preset, model, instruction, event):
            event.set()
            return 'late', 'ok'
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=cancel):
            stage, rows, result, status = self.run_batch()
        self.assertFalse(stage['complete'])
        self.assertFalse(stage['items'])
        self.assertIn('取消', status)
        self.assertIn('未处理 2', status)

    def test_processed_results_enqueue_as_direct_txt2img_jobs_without_second_llm_pass(self):
        self.db.save_prompt('first')
        self.db.save_prompt('second')
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=lambda text, *a: (text + ' polished', 'ok')):
            stage, *_ = self.run_batch()
        with mock.patch.object(self.ui, '_ensure_server_queue_worker'), mock.patch.object(self.ui._SERVER_QUEUE_WAKE, 'set'):
            queued_stage, batch_ids, message, gallery = self.ui._inline_cache_enqueue_results(
                stage, stage['items'][0]['record']['id'], 'first edited')
        self.assertEqual(len(batch_ids), 1)
        jobs = self.db.list_server_queue(batch_ids[0], 10)
        self.assertEqual([job['request'] for job in jobs], ['first edited', 'second polished'])
        self.assertTrue(all(job['target'] == 'txt2img' for job in jobs))
        self.assertTrue(all(job['config']['direct_prompt'] for job in jobs))
        self.assertIn('2 条', message)
        self.assertEqual(gallery, [])
        self.assertEqual(set(queued_stage['queued_ids']), {str(item['record']['id']) for item in stage['items']})
        _, second_batch_ids, second_message, _ = self.ui._inline_cache_enqueue_results(queued_stage, 0, '')
        self.assertEqual(second_batch_ids, batch_ids)
        self.assertIn('没有新的未入队', second_message)
        with mock.patch.object(self.ui, '_generate') as generate:
            prompt, status, direct = self.ui._server_queue_prepare_prompt(
                jobs[0], jobs[0]['config'], [], set(), mock.Mock(is_set=lambda: False))
        generate.assert_not_called()
        self.assertEqual((prompt, direct), ('first edited', True))
        self.assertIn('直接', status)

    def test_current_uses_edited_source_and_explicit_write(self):
        ident = self.db.save_prompt('original')
        record = self.db.get_prompt(ident)
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=lambda source, *a: (source + '!', 'ok')):
            stage, _, result, _ = self.run_batch(scope='当前记录', record=record, source='edited')
        self.assertEqual(result, 'edited!')
        self.ui._inline_cache_save(stage, '当前结果', ident, 'edited result')
        self.assertEqual(self.db.get_prompt(ident)['prompt'], 'edited result')
        self.assertEqual(self.ui._inline_cache_write('old', 'new', 'append')[0], 'old\nnew')
        self.assertEqual(self.ui._inline_cache_write('old', 'new', 'replace')[0], 'new')

    def test_result_page_clamps_and_selects_visible_record(self):
        stage = {'items': [{'record': {'id': i + 100}, 'result': f'prompt {i}'} for i in range(52)]}
        rows, selected, text, page = self.ui._inline_cache_result_view(stage, 99)
        self.assertEqual(page, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual((selected, text), (150, 'prompt 50'))
        self.assertEqual(self.ui._inline_cache_result_view({}, 99), ([], 0, '', 1))

    def test_save_view_removes_saved_rows_and_refreshes_current_snapshot(self):
        ident = self.db.save_prompt('original')
        record = self.db.get_prompt(ident)
        with mock.patch.object(self.ui, '_inline_cache_transform', return_value=('processed', 'ok')):
            stage, *_ = self.run_batch(scope='当前记录', record=record, source='edited')
        saved, message, rows, selected, text, page, current, source = self.ui._inline_cache_save_view(
            stage, '当前结果', ident, 'final edit', 8, record, 'edited')
        self.assertEqual((saved, rows, selected, text, page), ({}, [], 0, '', 1))
        self.assertEqual(current, self.db.get_prompt(ident))
        self.assertEqual(source, 'final edit')
        # A second pass uses the new snapshot and can be saved without a false stale conflict.
        with mock.patch.object(self.ui, '_inline_cache_transform', return_value=('second pass', 'ok')):
            next_stage, *_ = self.run_batch(scope='当前记录', record=current, source=source)
        self.assertEqual(self.ui._inline_cache_save(next_stage, '全部处理结果', 0, '')[0], {})

    def test_save_failure_keeps_unsaved_editor_and_selection(self):
        ident = self.db.save_prompt('original')
        record = self.db.get_prompt(ident)
        with mock.patch.object(self.ui, '_inline_cache_transform', return_value=('processed', 'ok')):
            stage, *_ = self.run_batch(scope='当前记录', record=record, source='edited')
        self.db.edit_prompts([record], 'prompt', 'set', '', 'concurrent edit')
        values = self.ui._inline_cache_save_view(stage, '当前结果', ident, 'unsaved edit', 1, record, 'source edit')
        self.assertIs(values[0], stage)
        self.assertEqual(values[3:], (ident, 'unsaved edit', 1, record, 'source edit'))

    def test_partial_save_leaves_remaining_results_selected(self):
        self.db.save_prompt('first')
        self.db.save_prompt('second')
        with mock.patch.object(self.ui, '_inline_cache_transform', side_effect=lambda text, *a: (text + '!', 'ok')):
            stage, *_ = self.run_batch()
        first_id = stage['items'][0]['record']['id']
        other_id = stage['items'][1]['record']['id']
        values = self.ui._inline_cache_save_view(stage, '当前结果', first_id, 'edited!', 1, {}, '')
        self.assertEqual(len(values[0]['items']), 1)
        self.assertEqual(values[3], other_id)
        self.assertEqual(self.db.get_prompt(first_id)['prompt'], 'edited!')
        self.assertNotIn('!', self.db.get_prompt(other_id)['prompt'])


if __name__ == '__main__':
    unittest.main()
