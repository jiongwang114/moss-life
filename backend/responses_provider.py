"""Responses wire protocol, including relays that require streaming and store=false."""
import json
import os
import httpx


def output_text(response):
    if response.get('status') not in (None, 'completed'):
        raise ValueError('response did not complete')
    text = []
    for item in response.get('output', []):
        if item.get('type') != 'message':
            continue
        for content in item.get('content', []):
            if content.get('type') == 'refusal':
                raise ValueError('provider refused the request')
            if content.get('type') == 'output_text':
                text.append(content.get('text', ''))
    return ''.join(text) or response.get('output_text', '')


async def generate_response(instruction, body, step, catalog):
    base = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    endpoint = base if base.endswith('/responses') else base + '/responses'
    result_schema = {'type': 'array', 'items': {'type': 'string', 'enum': [t['id'] for t in catalog]}} if step == 'tags' else {'type': 'string', 'minLength': 1}
    request = {
        'model': os.environ['LLM_MODEL'],
        'instructions': instruction,
        'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': body}]}],
        'store': False,
        'stream': True,
        'max_output_tokens': int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '32768')),
    }
    # Some relays do not implement structured output. Explicit configuration chooses the mode;
    # do not silently send the same private input twice via an alternate endpoint.
    mode = os.getenv('LLM_RESPONSE_FORMAT', 'json_schema')
    if mode == 'json_schema':
        request['text'] = {'format': {'type': 'json_schema', 'name': 'record_result', 'strict': True,
            'schema': {'type': 'object', 'properties': {'result': result_schema}, 'required': ['result'], 'additionalProperties': False}}}
    elif mode == 'json_object':
        request['text'] = {'format': {'type': 'json_object'}}
    elif mode != 'text':
        raise ValueError('unsupported response format')
    effort = os.getenv('LLM_REASONING_EFFORT')
    if effort:
        request['reasoning'] = {'effort': effort}
    timeout = httpx.Timeout(float(os.getenv('LLM_TIMEOUT_SECONDS', '120')), connect=15)
    headers = {'Authorization': 'Bearer ' + os.environ['LLM_API_KEY'], 'Accept': 'text/event-stream'}
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream('POST', endpoint, headers=headers, json=request) as response:
            response.raise_for_status()
            if 'text/event-stream' not in response.headers.get('content-type', ''):
                data = json.loads(await response.aread())
                text = output_text(data)
            else:
                text, chunks, completed, event_data = '', [], False, []

                def consume(lines):
                    nonlocal text, completed
                    value = '\n'.join(lines)
                    if not value or value == '[DONE]':
                        return
                    event = json.loads(value)
                    kind = event.get('type')
                    if kind in ('error', 'response.failed', 'response.incomplete'):
                        raise ValueError('provider response failed or incomplete')
                    if kind == 'response.output_text.delta':
                        chunks.append(event['delta'])
                    elif kind == 'response.completed':
                        text = output_text(event.get('response', {}))
                        completed = True

                async for line in response.aiter_lines():
                    if line.startswith('data:'):
                        event_data.append(line[5:].lstrip())
                    elif not line and event_data:
                        consume(event_data)
                        event_data = []
                if event_data:
                    consume(event_data)
                if not completed:
                    raise ValueError('response stream ended before completion')
                text = text or ''.join(chunks)
    if not text.strip():
        raise ValueError('provider returned no text')
    return text
