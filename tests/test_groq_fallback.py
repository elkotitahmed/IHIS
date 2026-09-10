"""Plan B language-model provider: Groq takes the call only when Gemini cannot."""
import unittest
from unittest import mock

import requests

from app import create_app, db
from app.models import AIUsageLog
from app.services.ai import platform
from app.services.ai.gemini_base import AIServiceError, GeminiBase

GEMINI_URL = 'generativelanguage.googleapis.com'
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'


def _groq_resp(text='plan b answer'):
    r = mock.Mock(); r.status_code = 200
    r.json.return_value = {'choices': [{'message': {'content': text}}]}
    r.raise_for_status.return_value = None
    return r


def _gemini_resp(text='gemini answer'):
    r = mock.Mock(); r.status_code = 200
    r.json.return_value = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
    r.raise_for_status.return_value = None
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all(); platform.reset_runtime_state()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop(); platform.reset_runtime_state()


class GroqFallbackTests(Base):
    def test_gemini_used_first_when_both_keys_exist(self):
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'g', 'GROQ_API_KEY': 'k'}), \
             mock.patch('requests.post', return_value=_gemini_resp()) as post:
            base = GeminiBase(); base.feature = 't'
            self.assertEqual(base._call_gemini('hi'), 'gemini answer')
            self.assertEqual(base.last_provider, 'gemini')
            self.assertIn(GEMINI_URL, post.call_args[0][0])

    def test_no_gemini_key_goes_straight_to_groq(self):
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': '', 'GROQ_API_KEY': 'k'}), \
             mock.patch('requests.post', return_value=_groq_resp()) as post:
            base = GeminiBase(system_prompt='sys'); base.feature = 't'
            self.assertTrue(base.available())
            self.assertEqual(base._call_gemini('hi'), 'plan b answer')
            self.assertEqual(base.last_provider, 'groq')
            self.assertEqual(post.call_args[0][0], GROQ_URL)
            self.assertEqual(post.call_args[1]['json']['messages'][0], {'role': 'system', 'content': 'sys'})
            self.assertNotIn('k', post.call_args[0][0])                       # key only in the header
        row = AIUsageLog.query.order_by(AIUsageLog.id.desc()).first()
        self.assertEqual((row.provider, row.status), ('groq', 'ok'))
        self.assertEqual(platform.status()['state'], 'READY')

    def test_gemini_failure_falls_back_to_groq(self):
        bad = requests.RequestException('boom'); bad.response = mock.Mock(status_code=500)
        calls = []
        def fake_post(url, **kw):
            calls.append(url)
            if GEMINI_URL in url:
                raise bad
            return _groq_resp('rescued')
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'g', 'GROQ_API_KEY': 'k', 'AI_FALLBACK_MODEL': ''}), \
             mock.patch('requests.post', side_effect=fake_post):
            base = GeminiBase(); base.feature = 't'
            self.assertEqual(base._call_gemini('hi'), 'rescued')
            self.assertEqual(base.last_provider, 'groq')
        self.assertTrue(any(GEMINI_URL in u for u in calls) and calls[-1] == GROQ_URL)
        rows = AIUsageLog.query.order_by(AIUsageLog.id).all()
        self.assertEqual([r.provider for r in rows][-2:], ['gemini', 'groq'])
        self.assertEqual(rows[-2].status, 'error'); self.assertEqual(rows[-1].status, 'ok')

    def test_both_fail_raises_gemini_message(self):
        bad = requests.RequestException('boom'); bad.response = mock.Mock(status_code=500)
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'g', 'GROQ_API_KEY': 'k', 'AI_FALLBACK_MODEL': ''}), \
             mock.patch('requests.post', side_effect=bad):
            base = GeminiBase(); base.feature = 't'
            with self.assertRaises(AIServiceError):
                base._call_gemini('hi')

    def test_run_ai_reports_groq_provider(self):
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': '', 'GROQ_API_KEY': 'k'}), \
             mock.patch('requests.post', return_value=_groq_resp('text from plan b')):
            res = platform.run_ai('feature.x', None, {'c': 1}, 'prompt', cache=False)
        self.assertEqual((res['status'], res['provider'], res['text']), ('ok', 'groq', 'text from plan b'))

    def test_no_keys_means_unavailable(self):
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': '', 'GROQ_API_KEY': ''}):
            self.assertFalse(GeminiBase().available())
            self.assertEqual(platform.status()['state'], 'LIMITED')


if __name__ == '__main__':
    unittest.main()
