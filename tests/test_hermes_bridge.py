"""Tests for the Hermes bridge HTTP client."""

from __future__ import annotations

import httpx
import respx

import career_copilot.hermes_bridge as hb
from career_copilot.hermes_bridge import HermesBridge, HermesBridgeError, get_bridge


@respx.mock
async def test_submit_returns_content() -> None:
    route = respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hello from Hermes"}}]},
        )
    )

    bridge = HermesBridge()
    result = await bridge.submit("hi")

    assert result == "Hello from Hermes"
    assert route.called


@respx.mock
async def test_submit_sends_system_prompt() -> None:
    route = respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )
    )

    bridge = HermesBridge()
    await bridge.submit("hi", system_prompt="Be concise.")

    body = route.calls[0].request.content.decode()
    assert '"system"' in body
    assert "Be concise" in body


@respx.mock
async def test_history_is_sent_on_follow_up() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "first answer"}}]},
            ),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "second answer"}}]},
            ),
        ]
    )

    bridge = HermesBridge()
    await bridge.submit("hello", chat_id="chat-1")
    await bridge.submit("yes", chat_id="chat-1")

    second_body = respx.calls[1].request.content.decode()
    assert "hello" in second_body
    assert "first answer" in second_body
    assert "yes" in second_body


@respx.mock
async def test_history_isolated_per_chat() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "a"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "b"}}]}),
        ]
    )

    bridge = HermesBridge()
    await bridge.submit("first", chat_id="chat-a")
    await bridge.submit("second", chat_id="chat-b")

    second_body = respx.calls[1].request.content.decode()
    assert "first" not in second_body


@respx.mock
async def test_clear_history() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "a"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "b"}}]}),
        ]
    )

    bridge = HermesBridge()
    await bridge.submit("first", chat_id="chat-1")
    bridge.clear_history("chat-1")
    await bridge.submit("second", chat_id="chat-1")

    second_body = respx.calls[1].request.content.decode()
    assert "first" not in second_body


def test_get_bridge_is_singleton() -> None:
    hb._bridge = None  # reset for test isolation
    a = get_bridge()
    b = get_bridge()
    assert a is b
    hb._bridge = None


@respx.mock
async def test_singleton_history_persists_across_submits() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "first answer"}}]},
            ),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "second answer"}}]},
            ),
        ]
    )
    hb._bridge = None  # reset for test isolation
    bridge = get_bridge()
    await bridge.submit("hello", chat_id="chat-s")
    await bridge.submit("yes", chat_id="chat-s")
    assert "hello" in respx.calls[1].request.content.decode()
    hb._bridge = None


@respx.mock
async def test_submit_raises_on_non_200() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom")
    )

    bridge = HermesBridge()
    try:
        await bridge.submit("hi")
        assert False, "expected HermesBridgeError"
    except HermesBridgeError as exc:
        assert "500" in str(exc)


@respx.mock
async def test_submit_raises_on_empty_content() -> None:
    respx.post("http://127.0.0.1:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )
    )

    bridge = HermesBridge()
    try:
        await bridge.submit("hi")
        assert False, "expected HermesBridgeError"
    except HermesBridgeError as exc:
        assert "empty" in str(exc)
