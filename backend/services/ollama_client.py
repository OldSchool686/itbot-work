import asyncio
import logging
from typing import Optional, List

import httpx
from backend.utils.config import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Ollama API client for local LLM inference."""

    def __init__(self, base_url: str = ""):
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model_chat = settings.ollama_model_chat
        self._model_embed = settings.ollama_model_embed
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self._client = httpx.AsyncClient(timeout=120.0, limits=limits)

    async def _request(self, endpoint: str, payload: dict, timeout_s: float) -> httpx.Response:
        """Make a request to Ollama API with retry logic."""
        last_error = None
        for attempt in range(1, 4):
            try:
                resp = await self._client.post(
                    f"{self._base_url}{endpoint}",
                    json=payload,
                )
                return resp
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(f"Ollama request attempt {attempt} failed: {e}")
                if attempt < 3:
                    await asyncio.sleep(1 * attempt)
        raise RuntimeError(f"Ollama API unavailable after 3 attempts: {last_error}")

    async def close(self):
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def generate(self, model: str = "", prompt: str = "") -> str:
        """Generate text completion via Ollama /api/generate."""
        resp = await self._request(
            "/api/generate",
            {"model": model or self._model_chat, "prompt": prompt, "stream": False},
            timeout_s=60.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    async def embed(self, text: str) -> List[float]:
        """Generate embedding vector via Ollama /api/embeddings."""
        resp = await self._request(
            "/api/embeddings",
            {"model": self._model_embed, "prompt": text},
            timeout_s=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [])

    async def chat(self, model: str = "", messages: Optional[List[dict]] = None) -> str:
        """Chat completion via Ollama /api/chat."""
        resp = await self._request(
            "/api/chat",
            {
                "model": model or self._model_chat, 
                "messages": messages or [], 
                "stream": False,
                "options": {
                    "temperature": 0.0,      # Строгий официальный тон
                    #"top_p": 0.8,            # Рекомендация HuggingFace
                    #"top_k": 20,
                    #"presence_penalty": 1.5, # Критично! Предотвращает зацикливание и ускоряет ответ
                    #"num_predict": 1000,       # Лимит длины ответа (чтобы не висело по 2 минуты)
                    #"enable_thinking": False
                }
            },
            timeout_s=120.0,
        )
        resp.raise_for_status()
        message = resp.json().get("message", {})
        return message.get("content", "")


_ollama_client: Optional[OllamaClient] = None


def get_ollama_client() -> OllamaClient:
    """Get or create the Ollama client instance."""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = OllamaClient()
    return _ollama_client
