"""Short-term conversational memory in Redis.

Per-conversation message window with sliding TTL. Stored as a Redis list,
RPUSHed on each turn, LTRIMmed to a maximum length, and EXPIREd to refresh
the TTL on every write.

Contracts and invariants are documented in docs/SPEC.md §10.

Owner: Person B.
"""

from __future__ import annotations

import time
from typing import Literal
from uuid import UUID

import structlog
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

Role = Literal["visitor", "assistant", "tool"]


class MemoryEntry(BaseModel):
    """One turn in a conversation's short-term memory window.

    The shape is deliberately small; tool arguments and tool results are
    NOT stored. Identity (tenant_id, conversation_id) lives in the Redis
    key, never in the value.

    ``content`` holds the original visitor/assistant text so the LLM sees
    real conversation context on subsequent turns (e.g. a real business
    email rather than ``[REDACTED_EMAIL]``). Log-grade redaction is applied
    at the structlog / OpenTelemetry layers, and the durable
    ``messages.content_redacted`` SQL column continues to be redacted by
    ``ChatOrchestrator`` before write — those compliance paths are
    independent of this Redis-only short-term cache.

    ``validation_alias`` accepts the legacy ``content_redacted`` JSON key
    so already-cached Redis entries from before this change still load
    (they fade naturally via the sliding TTL).
    """

    model_config = ConfigDict(populate_by_name=True)

    role: Role
    content: str = Field(
        ...,
        validation_alias=AliasChoices("content", "content_redacted"),
    )
    ts: int


def _key(tenant_id: UUID, conversation_id: UUID) -> str:
    """SPEC §10 key shape: memory:{tenant_id}:{conversation_id}."""
    return f"memory:{tenant_id}:{conversation_id}"


def _tenant_pattern(tenant_id: UUID) -> str:
    return f"memory:{tenant_id}:*"


class MemoryService:
    """Redis-backed short-term conversational memory.

    Invariants:
      - Values passed to append() are stored verbatim so the LLM receives
        the original conversation context on subsequent turns. PII / secret
        redaction for *logs, spans, and durable DB writes* happens in the
        respective sinks (``app.core.tracing`` / structlog processors /
        ``ChatOrchestrator``'s DB write path), independent of this cache.
      - LTRIM keeps the list bounded to max_entries on every append.
      - EXPIRE refreshes the TTL on every append (sliding window).
      - Redis errors fail OPEN: load() returns []; append/purge log + return.
      - purge_tenant uses SCAN + UNLINK, never KEYS + DEL.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        max_entries: int,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._max_entries = max_entries

    async def append(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        role: Role,
        content: str,
    ) -> None:
        """Append one entry to the conversation window (original content)."""
        entry = MemoryEntry(
            role=role,
            content=content,
            ts=int(time.time()),
        )
        key = _key(tenant_id, conversation_id)

        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.rpush(key, entry.model_dump_json())
                pipe.ltrim(key, -self._max_entries, -1)
                pipe.expire(key, self._ttl)
                await pipe.execute()
        except RedisError as exc:
            logger.warning(
                "memory_append_failed",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                role=role,
                error=str(exc),
            )

    async def load(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> list[MemoryEntry]:
        """Return entries for the conversation in chronological order.

        The list is bounded by LTRIM at write time, so the full range is safe
        to return. Returns [] on Redis errors (fail-open).
        """
        key = _key(tenant_id, conversation_id)
        try:
            raw = await self._redis.lrange(key, 0, -1)
        except RedisError as exc:
            logger.warning(
                "memory_load_failed",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                error=str(exc),
            )
            return []

        entries: list[MemoryEntry] = []
        for item in raw:
            try:
                entries.append(MemoryEntry.model_validate_json(item))
            except (ValueError, TypeError):
                logger.warning(
                    "memory_entry_corrupt",
                    tenant_id=str(tenant_id),
                    conversation_id=str(conversation_id),
                )
        return entries

    async def purge_conversation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> None:
        """Delete one conversation's memory key. Non-blocking via UNLINK."""
        key = _key(tenant_id, conversation_id)
        try:
            await self._redis.unlink(key)
        except RedisError as exc:
            logger.warning(
                "memory_purge_conversation_failed",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                error=str(exc),
            )

    async def purge_tenant(self, tenant_id: UUID) -> int:
        """Delete every memory key for a tenant. Returns the count deleted.

        Uses SCAN + UNLINK so a large keyspace does not stall Redis. Called
        by ErasureService during right-to-erasure flows.
        """
        pattern = _tenant_pattern(tenant_id)
        deleted = 0
        batch: list[str] = []

        try:
            async for key in self._redis.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) >= 500:
                    deleted += await self._redis.unlink(*batch)
                    batch.clear()
            if batch:
                deleted += await self._redis.unlink(*batch)
        except RedisError as exc:
            logger.warning(
                "memory_purge_tenant_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
                keys_deleted_before_error=deleted,
            )
            return deleted

        logger.info(
            "memory_purge_tenant",
            tenant_id=str(tenant_id),
            keys_deleted=deleted,
        )
        return deleted
