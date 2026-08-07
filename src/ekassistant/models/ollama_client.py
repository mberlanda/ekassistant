"""Thin wrapper around the local Ollama API.

See ADR-0004 (LLM) and ADR-0006 (embedding model): both model names come
from settings, never hardcoded here, so swapping either is a config change.
"""

import ollama

from ekassistant.config.settings import Settings


class OllamaClient:
    def __init__(self, settings: Settings):
        self._client = ollama.Client(host=settings.ollama_base_url)
        self._chat_model = settings.ollama_model
        self._embed_model = settings.ollama_embed_model

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embed(model=self._embed_model, input=texts)
        return list(response["embeddings"])

    def chat_json(self, system: str, user: str, json_schema: dict) -> str:
        response = self._client.chat(
            model=self._chat_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=json_schema,
        )
        return response["message"]["content"]
