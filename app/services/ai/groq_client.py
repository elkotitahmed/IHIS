"""Groq (OpenAI-compatible chat completions) — plan B for the language model.

Used only when Gemini is not configured or a Gemini call fails (network,
quota, 5xx). Same contract as the Gemini call: a single prompt in, text out.
The key lives in ``GROQ_API_KEY`` (never logged, never in a URL); the model in
``GROQ_MODEL`` (default: ``openai/gpt-oss-120b``).
"""
import os

import requests

BASE_URL = 'https://api.groq.com/openai/v1/chat/completions'
DEFAULT_MODEL = 'openai/gpt-oss-120b'
REQUEST_TIMEOUT = 45


def groq_available():
    return bool(os.getenv('GROQ_API_KEY'))


def model_name():
    return os.getenv('GROQ_MODEL', DEFAULT_MODEL)


def chat(system_prompt, user_message, temperature=0.4, max_tokens=3000, timeout=REQUEST_TIMEOUT):
    """Return the assistant text. Raises ``requests.RequestException`` or
    ``ValueError`` (malformed response); callers translate to AIServiceError."""
    key = os.getenv('GROQ_API_KEY')
    if not key:
        raise RuntimeError('GROQ_API_KEY not configured')
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_message})
    body = {'model': model_name(), 'messages': messages, 'temperature': temperature,
            'max_tokens': max_tokens}
    if model_name().startswith('openai/gpt-oss'):
        # Reasoning models spend the token budget on hidden reasoning first;
        # keep it low so the visible answer always fits.
        body['reasoning_effort'] = 'low'
    resp = requests.post(
        BASE_URL,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data['choices'][0]['message']['content'] or ''
    except (KeyError, IndexError, TypeError):
        raise ValueError('malformed response')
