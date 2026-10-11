"""Private training rows stay outside the repo and contain no secrets."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recorder import (  # noqa: E402
    TickLogger,
    cmd_stats,
    local_training_path,
    recording_enabled,
    outside_repo,
    repo_root,
    sanitize_entry,
    training_log_path,
)


class RecorderPrivacyTest(unittest.TestCase):
    def test_training_path_is_outside_the_repo_and_secrets_are_dropped(self):
        path = local_training_path()
        self.assertTrue(outside_repo(path))
        self.assertTrue(path.startswith(os.path.expanduser("~/.cowcraft/training")))
        self.assertFalse(outside_repo(os.path.join(repo_root(), "session.jsonl")))
        redirected = training_log_path(os.path.join(repo_root(), "session.jsonl"))
        self.assertTrue(outside_repo(redirected))
        cleaned = sanitize_entry({
            "action": "loot",
            "password": "hunter2",
            "connection": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee." + ("ab" * 32),
            "text": "token aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee." + ("cd" * 32),
            "nested": {"token": "secret-token", "note": "ok"},
        })
        self.assertEqual(cleaned["action"], "loot")
        self.assertNotIn("password", cleaned)
        self.assertNotIn("connection", cleaned)
        self.assertNotIn("eeeeeeeeeeee", cleaned["text"])
        self.assertNotIn("token", cleaned["nested"])
        self.assertEqual(cleaned["nested"]["note"], "ok")
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "rows.jsonl")
            logger = TickLogger(log_path)
            logger.log({
                "type": "tick",
                "password": "nope",
                "action": "loot",
                "confidence": 0.8,
                "model": "main-model",
                "progressed": True,
                "advice": "not required",
            })
            logger.close()
            logger.close()
            self.assertEqual(os.stat(log_path).st_mode & 0o777, 0o600)
            with open(log_path) as handle:
                row = json.loads(handle.read().splitlines()[0])
            self.assertEqual(row["action"], "loot")
            self.assertNotIn("password", row)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_stats(SimpleNamespace(in_file=log_path))
            text = buf.getvalue()
            self.assertIn("loot", text)
            self.assertIn("progressed acts: 1", text)
            self.assertIn("main-model", text)
        self.assertFalse(recording_enabled({}))
        self.assertFalse(recording_enabled({}, {"COWCRAFT_RECORD": "0"}))
        self.assertFalse(recording_enabled({"record": False}, {"COWCRAFT_RECORD": "1"}))
        self.assertTrue(recording_enabled({"record": True}))
        self.assertTrue(recording_enabled({}, {"COWCRAFT_RECORD": "1"}))
        print("RECORDER training rows stay outside the repo and drop secrets")


if __name__ == "__main__":
    unittest.main(verbosity=2)
