import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("runner", Path(__file__).with_name("runner.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SafetyTests(unittest.TestCase):
    def post(self, text="O sujeito anuncia compromisso com a família. A louça aguarda a coletiva.", **kwargs):
        return {"text": text, "category": "atemporal", "requires_approval": False, **kwargs}

    def test_good_post(self):
        self.assertEqual(m.validate_post(self.post(), []), self.post()["text"])

    def test_reject_news(self):
        with self.assertRaises(ValueError):
            m.validate_post(self.post(category="requires_review"), [])

    def test_reject_approval(self):
        with self.assertRaises(ValueError):
            m.validate_post(self.post(requires_approval=True), [])

    def test_reject_approval_string(self):
        with self.assertRaises(ValueError):
            m.validate_post(self.post(requires_approval="false"), [])

    def test_length(self):
        for text in ["", "x" * 281, " Texto."]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.validate_post(self.post(text), [])

    def test_forbidden_characters(self):
        for text in ["Olá @perfil.", "Uma ideia #tag.", "Risos 😀.", "Teste\n::error::x", "Terapia custa 500.", "Um link https://example.com.", "Será que funciona?", "Olha isso!"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.validate_post(self.post(text), [])

    def test_reject_unknown_keys(self):
        with self.assertRaises(ValueError):
            m.validate_post(self.post(command="execute"), [])

    def test_reject_duplicate(self):
        with self.assertRaises(ValueError):
            m.validate_post(self.post(), [self.post()["text"]])

    def test_auth_chatgpt(self):
        fake = {"auth_mode": "chatgpt", "tokens": {"access_token": "TEST_ONLY", "refresh_token": "TEST_ONLY"}}
        self.assertEqual(m.auth_ok(json.dumps(fake))["auth_mode"], "chatgpt")

    def test_auth_api_not_allowed(self):
        for fake in [{"auth_mode": "api"}, {"auth_mode": "chatgpt", "OPENAI_API_KEY": "TEST_ONLY", "tokens": {"access_token": "TEST", "refresh_token": "TEST"}}]:
            with self.assertRaises(ValueError):
                m.auth_ok(json.dumps(fake))

    def test_auth_without_refresh(self):
        with self.assertRaises(ValueError):
            m.auth_ok(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "TEST"}}))

    def test_model_does_not_receive_secrets(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "TEST", "GH_TOKEN": "TEST", "CODEX_SECRET_WRITER": "TEST", "BUFFER_API_KEY": "TEST", "OPENAI_API_KEY": "TEST", "CODEX_API_KEY": "TEST", "GITHUB_OUTPUT": "/tmp/protected", "CODEX_HOME": "/tmp/codex-test"}, clear=True):
            self.assertEqual(m.restricted_env(), {"CODEX_HOME": "/tmp/codex-test"})

    def test_private_repository_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            env = {"GITHUB_REPOSITORY": m.REPO, "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "schedule", "GITHUB_EVENT_PATH": str(path)}
            with patch.dict(os.environ, env, clear=True):
                path.write_text(json.dumps({"repository": {"private": False}}))
                with self.assertRaises(RuntimeError):
                    m.github_guard()
                path.write_text(json.dumps({"repository": {"private": True}}))
                self.assertTrue(m.github_guard()["repository"]["private"])
                os.environ["GITHUB_REF"] = "refs/heads/unsafe"
                with self.assertRaises(RuntimeError):
                    m.github_guard()

    def test_history_two_scheduled_proofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "codex-trials"
            folder.mkdir()
            for idx in range(2):
                (folder / f"{idx}.json").write_text(json.dumps({"text": f"sample {idx}", "event": "schedule", "mode": "dry_run"}))
            texts, count = m.read_history(root)
            self.assertEqual(count, 2)
            self.assertEqual(len(texts), 2)

    def test_source_has_no_buffer_endpoint(self):
        source = Path(m.__file__).read_text()
        self.assertNotIn("api.buffer.com", source)
        self.assertNotIn("shareNow", source)
        self.assertNotIn("danger-full-access", source)


if __name__ == "__main__":
    unittest.main()
