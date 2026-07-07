import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from backend.services.ollama_client import OllamaClient


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level singleton before each test."""
    from backend.services.ollama_client import get_ollama_client
    import backend.services.ollama_client as mod
    mod._ollama_client = None
    yield
    mod._ollama_client = None


@pytest.fixture
def client():
    return OllamaClient(base_url="http://test:11434")


class TestGenerate:

    @pytest.mark.asyncio
    async def test_generate_default_model(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Hello world"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)):
            result = await client.generate(prompt="say hello")
            assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_generate_custom_model(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Custom response"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.generate(model="llama3", prompt="test")
            call_args = mock_req.call_args
            payload = call_args[0][1]
            assert payload["model"] == "llama3"

    @pytest.mark.asyncio
    async def test_generate_request_format(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "OK"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.generate(prompt="test prompt")
            call_args = mock_req.call_args
            assert call_args[0][0] == "/api/generate"
            payload = call_args[0][1]
            assert payload["prompt"] == "test prompt"
            assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_generate_timeout(self):
        from backend.services.ollama_client import OllamaClient
        c = OllamaClient(base_url="http://test:11434")

        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
            with pytest.raises(RuntimeError, match="Ollama API unavailable after 3 attempts"):
                await c.generate(prompt="test")


class TestEmbed:

    @pytest.mark.asyncio
    async def test_embed_returns_vector(self, client):
        expected = [0.1] * 768
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": expected}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)):
            result = await client.embed("test text")
            assert len(result) == 768
            assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_embed_request_format(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": [0.0]}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.embed("hello")
            call_args = mock_req.call_args
            assert call_args[0][0] == "/api/embeddings"
            payload = call_args[0][1]
            assert payload["prompt"] == "hello"
            assert payload["model"] == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_embed_dimension_consistency(self, client):
        dims = 512
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": [0.1] * dims}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)):
            r1 = await client.embed("text one")
            r2 = await client.embed("text two")
            assert len(r1) == len(r2) == dims


class TestChat:

    @pytest.mark.asyncio
    async def test_chat_returns_content(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "AI response"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)):
            result = await client.chat(messages=[{"role": "user", "content": "hi"}])
            assert result == "AI response"

    @pytest.mark.asyncio
    async def test_chat_request_format(self, client):
        messages = [{"role": "user", "content": "hello"}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "OK"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.chat(messages=messages)
            call_args = mock_req.call_args
            assert call_args[0][0] == "/api/chat"

    @pytest.mark.asyncio
    async def test_chat_with_messages(self, client):
        messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "response"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.chat(messages=messages)
            payload = mock_req.call_args[0][1]
            assert payload["messages"] == messages
            assert payload["stream"] is False


class TestRetryLogic:

    @pytest.mark.asyncio
    async def test_retry_on_request_error(self):
        from backend.services.ollama_client import OllamaClient
        c = OllamaClient(base_url="http://test:11434")

        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.RequestError("conn refused"))):
            with pytest.raises(RuntimeError, match="Ollama API unavailable after 3 attempts"):
                await c.generate(prompt="test")

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        from backend.services.ollama_client import OllamaClient
        c = OllamaClient(base_url="http://test:11434")

        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
            with pytest.raises(RuntimeError, match="Ollama API unavailable after 3 attempts"):
                await c.embed("test")


class TestSingleton:

    def test_get_ollama_client_returns_same_instance(self):
        from backend.services.ollama_client import get_ollama_client
        c1 = get_ollama_client()
        c2 = get_ollama_client()
        assert c1 is c2

    def test_singleton_uses_config_defaults(self):
        from backend.services.ollama_client import get_ollama_client, OllamaClient
        client = get_ollama_client()
        assert isinstance(client, OllamaClient)


class TestTimeouts:

    @pytest.mark.asyncio
    async def test_generate_timeout_is_60s(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "OK"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.generate(prompt="test")
            assert mock_req.call_args.kwargs["timeout_s"] == 60.0

    @pytest.mark.asyncio
    async def test_embed_timeout_is_10s(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": [0.0]}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.embed("test")
            assert mock_req.call_args.kwargs["timeout_s"] == 10.0

    @pytest.mark.asyncio
    async def test_chat_timeout_is_60s(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "OK"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
            await client.chat(messages=[{"role": "user", "content": "hi"}])
            assert mock_req.call_args.kwargs["timeout_s"] == 60.0
