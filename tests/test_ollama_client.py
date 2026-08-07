from ekassistant.config.settings import Settings
from ekassistant.models.ollama_client import OllamaClient


class FakeUnderlyingClient:
    """Stand-in for ollama.Client so tests never need a running Ollama server."""

    def __init__(self, host: str):
        self.host = host
        self.embed_calls: list[dict] = []
        self.chat_calls: list[dict] = []

    def embed(self, model: str, input):  # noqa: A002 - matches ollama.Client's signature
        self.embed_calls.append({"model": model, "input": input})
        texts = input if isinstance(input, list) else [input]
        return {"embeddings": [[0.1, 0.2] for _ in texts]}

    def chat(self, model: str, messages, format):  # noqa: A002 - matches ollama.Client's signature
        self.chat_calls.append({"model": model, "messages": messages, "format": format})
        return {"message": {"content": '{"answer": "", "citations": [], "abstained": true}'}}


def _make_client(monkeypatch) -> tuple[OllamaClient, FakeUnderlyingClient]:
    fake = FakeUnderlyingClient(host="unused")
    monkeypatch.setattr("ekassistant.models.ollama_client.ollama.Client", lambda host: fake)
    settings = Settings(ollama_model="test-chat-model", ollama_embed_model="test-embed-model")
    return OllamaClient(settings), fake


def test_embed_uses_configured_embed_model(monkeypatch):
    client, fake = _make_client(monkeypatch)
    vector = client.embed("hello")
    assert vector == [0.1, 0.2]
    assert fake.embed_calls[0]["model"] == "test-embed-model"


def test_embed_batch_returns_one_vector_per_input(monkeypatch):
    client, fake = _make_client(monkeypatch)
    vectors = client.embed_batch(["a", "b", "c"])
    assert len(vectors) == 3
    assert fake.embed_calls[0]["input"] == ["a", "b", "c"]


def test_chat_json_uses_configured_chat_model_and_schema(monkeypatch):
    client, fake = _make_client(monkeypatch)
    schema = {"type": "object"}
    content = client.chat_json(system="sys", user="usr", json_schema=schema)
    assert content == '{"answer": "", "citations": [], "abstained": true}'
    assert fake.chat_calls[0]["model"] == "test-chat-model"
    assert fake.chat_calls[0]["format"] == schema
    assert fake.chat_calls[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
