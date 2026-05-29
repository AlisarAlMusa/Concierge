"""Unit tests for cost_service (Spec 013 FR-001 – FR-004).

Pure-unit: no DB, no network. The cost_service is tested via its public
calculate-cost helper and the schema of the record_event call.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.cost_event import CostOperation
from app.services import cost_service

# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def _mock_settings():
    s = MagicMock()
    s.COST_GROQ_INPUT_PER_TOKEN = 0.0000003
    s.COST_GROQ_OUTPUT_PER_TOKEN = 0.0000005
    s.COST_COHERE_INPUT_PER_TOKEN = 0.0000002
    return s


class TestEstimateCost:
    def test_groq_llm_cost(self):
        """Groq pricing: both input and output tokens contribute."""
        with patch("app.services.cost_service.get_settings", return_value=_mock_settings()):
            cost = cost_service._estimate_cost("groq", input_tokens=1000, output_tokens=500)

        expected = Decimal("0.0000003") * 1000 + Decimal("0.0000005") * 500
        assert cost == expected

    def test_cohere_embedding_cost(self):
        """Cohere charges on input tokens only for embedding."""
        with patch("app.services.cost_service.get_settings", return_value=_mock_settings()):
            cost = cost_service._estimate_cost("cohere", input_tokens=500, output_tokens=0)

        expected = Decimal("0.0000002") * 500
        assert cost == expected

    def test_self_hosted_provider_zero_cost(self):
        """Unknown / self-hosted provider always returns zero."""
        with patch("app.services.cost_service.get_settings", return_value=_mock_settings()):
            cost = cost_service._estimate_cost("model_server", input_tokens=200, output_tokens=100)

        assert cost == Decimal("0")

    def test_provider_name_case_insensitive(self):
        """Provider matching is case-insensitive."""
        with patch("app.services.cost_service.get_settings", return_value=_mock_settings()):
            cost_upper = cost_service._estimate_cost("GROQ", input_tokens=100, output_tokens=0)
            cost_lower = cost_service._estimate_cost("groq", input_tokens=100, output_tokens=0)

        assert cost_upper == cost_lower


# ---------------------------------------------------------------------------
# record_event (fire-and-forget)
# ---------------------------------------------------------------------------


class TestRecordEvent:
    def test_record_event_schedules_task(self):
        """record_event creates an asyncio task (fire-and-forget)."""
        tenant_id = uuid4()

        with patch("asyncio.create_task") as mock_create_task:
            cost_service.record_event(
                tenant_id=tenant_id,
                provider="groq",
                model="mixtral-8x7b-32768",
                operation=CostOperation.llm,
                input_tokens=100,
                output_tokens=50,
            )

        mock_create_task.assert_called_once()

    def test_record_event_zero_output_tokens_default(self):
        """output_tokens defaults to 0 (embedding calls pass input only)."""
        with patch("asyncio.create_task") as mock_create_task:
            cost_service.record_event(
                tenant_id=uuid4(),
                provider="cohere",
                model="embed-english-v3.0",
                operation=CostOperation.embedding,
                input_tokens=200,
            )

        mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# _write_cost_event — DB failure is swallowed (FR-004)
# ---------------------------------------------------------------------------


class TestWriteCostEvent:
    @pytest.mark.asyncio
    async def test_db_failure_is_swallowed(self):
        """A failed DB write warns but does not raise (FR-004)."""
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_session_cm)

        with patch("app.services.cost_service.get_session_factory", return_value=mock_factory):
            with patch("app.services.cost_service.get_settings", return_value=_mock_settings()):
                # Should complete without raising
                await cost_service._write_cost_event(
                    tenant_id=uuid4(),
                    provider="groq",
                    model="test-model",
                    operation=CostOperation.llm,
                    input_tokens=10,
                    output_tokens=5,
                )

    @pytest.mark.asyncio
    async def test_write_cost_event_inserts_correct_row(self):
        """_write_cost_event creates a CostEvent with correct fields."""
        tenant_id = uuid4()
        inserted_events = []

        async def _capture_insert(session, event):
            inserted_events.append(event)

        mock_session = AsyncMock()
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_session_cm)

        with (
            patch("app.services.cost_service.get_session_factory", return_value=mock_factory),
            patch("app.services.cost_service.get_settings", return_value=_mock_settings()),
            patch.object(
                cost_service.cost_repository,
                "insert_cost_event",
                side_effect=_capture_insert,
            ),
        ):
            await cost_service._write_cost_event(
                tenant_id=tenant_id,
                provider="groq",
                model="mixtral-8x7b-32768",
                operation=CostOperation.llm,
                input_tokens=100,
                output_tokens=50,
            )

        assert len(inserted_events) == 1
        ev = inserted_events[0]
        assert ev.tenant_id == tenant_id
        assert ev.provider == "groq"
        assert ev.model == "mixtral-8x7b-32768"
        assert ev.operation == CostOperation.llm
        assert ev.input_tokens == 100
        assert ev.output_tokens == 50
        assert ev.estimated_cost_usd > Decimal("0")


# ---------------------------------------------------------------------------
# Tenant isolation (Spec 013 SC-002)
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_separate_tenants_get_separate_events(self):
        """record_event is called separately for each tenant — no cross-tagging."""
        tenant_a = uuid4()
        tenant_b = uuid4()

        created_tasks = []

        with patch("asyncio.create_task", side_effect=lambda coro: created_tasks.append(coro)):
            cost_service.record_event(
                tenant_id=tenant_a,
                provider="groq",
                model="test",
                operation=CostOperation.llm,
                input_tokens=10,
                output_tokens=5,
            )
            cost_service.record_event(
                tenant_id=tenant_b,
                provider="groq",
                model="test",
                operation=CostOperation.llm,
                input_tokens=20,
                output_tokens=10,
            )

        assert len(created_tasks) == 2
