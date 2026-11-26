import json
import unittest
from io import StringIO
from unittest.mock import Mock

from scripts import run_d2_prompt as runner


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "model": "qwen2.5:3b", "model_digest": "digest",
            "options": {"temperature": 0.6, "num_ctx": 4096, "num_predict": 128},
            "templates": {name: "{{OBSERVATIONS}}\n{{PRIOR_EXPRESSIONS}}" for name in ("P0", "P1", "P2")},
            "blocks": [{"id": "block", "group_id": "group", "observations": "(x=0, y=0) -> 0",
                        "seed": 42, "slot_seeds": [42, 43, 44, 45], "prompt_order": ["P0", "P1", "P2"]}],
            "block_rerun_min_failures": 2,
        }
        self.parse = lambda text: text.removeprefix("Expression: ").strip() if text else None
        self.body = json.dumps({"done": True, "response": "Expression: z", "done_reason": "stop"}).encode()

    def test_paired_slots_write_once_and_keep_guard_rejected_original(self):
        log = StringIO()
        post = Mock(return_value=(200, self.body, None))
        runner.collect(self.config, log, post, self.parse, lambda _: False)
        records = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual(post.call_count, 12)
        self.assertEqual(len(records), 12)
        for slot in range(1, 5):
            paired = [record for record in records if record["slot"] == slot]
            self.assertEqual({record["request"]["options"]["seed"] for record in paired}, {41 + slot})
        self.assertTrue(all(record["response"] == "Expression: z" for record in records))
        self.assertTrue(all(not record["guard_pass"] for record in records))
        self.assertTrue(all(record["request"]["options"]["temperature"] == 0.6 for record in records))
        self.assertIn("Expression: z", records[3]["request"]["prompt"])

    def test_two_infrastructure_failures_rerun_the_whole_block_once(self):
        log = StringIO()
        post = Mock(side_effect=[(503, b"busy", None), (None, b"", "timeout")] + [(200, self.body, None)] * 22)
        runner.collect(self.config, log, post, self.parse, lambda _: True)
        records = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual(post.call_count, 24)
        self.assertEqual([record["attempt"] for record in records], [1] * 12 + [2] * 12)
        self.assertEqual([record["http_status"] for record in records[:2]], [503, None])
        self.assertEqual(
            [record["request"]["options"]["seed"] for record in records[:12]],
            [record["request"]["options"]["seed"] for record in records[12:]],
        )

    def test_lockdown_rejects_new_file_reads_and_cannot_be_removed_by_runner(self):
        for event in ("open", "os.listdir", "subprocess.Popen", "ctypes.dlopen"):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                runner.file_lockdown(event, ())
