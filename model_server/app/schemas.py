# Contracts defined in docs/SPEC.md §4. Do not change without team consensus.
from uuid import UUID

from pydantic import BaseModel


class PredictRequest(BaseModel):
    message: str
    tenant_id: UUID


class PredictResponse(BaseModel):
    label: str
    confidence: float
    model_version: str


class LeadScoreRequest(BaseModel):
    message: str
    tenant_id: UUID


class LeadScoreResponse(BaseModel):
    score: float
    model_version: str
