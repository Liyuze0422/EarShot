"""Minimal direct DeepSeek fallback for the relay-first answer path."""
import os


def _key(settings, ref="TP_API_KEY"):
    value = (os.environ.get(ref) or "").strip()
    if value:
        return value
    return settings.api_key()


def stream(settings, base_url, model, messages, timeout, retries):
    """Return an OpenAI-compatible direct stream; caller owns iteration."""
    from openai import OpenAI
    with OpenAI(api_key=_key(settings), base_url=base_url,
                timeout=timeout, max_retries=retries) as client:
        with client.chat.completions.create(
                model=model, messages=messages, stream=True,
                temperature=0.2, max_tokens=400,
                extra_body={'thinking': {'type': 'disabled'}}) as response:
            yield from response


def warmup(settings, base_url, model, timeout, retries):
    from openai import OpenAI
    with OpenAI(api_key=_key(settings), base_url=base_url,
                timeout=timeout, max_retries=retries) as client:
        return client.chat.completions.create(
            model=model, messages=[{'role': 'user', 'content': 'ok'}],
            max_tokens=1, extra_body={'thinking': {'type': 'disabled'}})
