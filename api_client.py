#!/usr/bin/env python3
"""
API client (runs on A - the user's machine).

Simple wrapper to call the LLM API through the relay.
Compatible with OpenAI-style API. Also usable with the openai Python library:

    import openai
    client = openai.OpenAI(
        api_key="sk-llmrouter-default-token-change-me",
        base_url="https://RELAY_IP:443/v1",
    )
"""

import json
import ssl
import sys

import aiohttp
import asyncio

import config


async def chat(prompt: str, model: str = "default", stream: bool = False) -> str:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    url = f"https://{config.RELAY_ADDR}:{config.RELAY_PORT}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.AUTH_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if stream:
                full = []
                async for line in resp.content:
                    decoded = line.decode().strip()
                    if decoded.startswith("data: ") and decoded != "data: [DONE]":
                        try:
                            chunk = json.loads(decoded[6:])
                            delta = chunk["choices"][0].get("delta", {}).get("content", "")
                            full.append(delta)
                            print(delta, end="", flush=True)
                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass
                print()
                return "".join(full)
            else:
                result = await resp.json()
                if "error" in result:
                    return f"Error: {result['error']}"
                return result["choices"][0]["message"]["content"]


async def main():
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello, who are you?"
    stream = "--stream" in sys.argv
    if stream:
        prompt = prompt.replace("--stream", "").strip()
    response = await chat(prompt, stream=stream)
    if not stream:
        print(response)


if __name__ == "__main__":
    asyncio.run(main())
