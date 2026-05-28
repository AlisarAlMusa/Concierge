"""FastAPI dependency accessors for `model_server`."""

from __future__ import annotations

from fastapi import Request

from app.core.model_loader import ModelLoader
from app.services.embedding_client import CohereEmbeddingClient


def get_loader(request: Request) -> ModelLoader:
    return request.app.state.loader


def get_embedder(request: Request) -> CohereEmbeddingClient:
    return request.app.state.embedder
