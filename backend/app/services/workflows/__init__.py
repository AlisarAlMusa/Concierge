"""Workflow services — deterministic, single-step alternatives to AgentService.

Used by ``ChatOrchestrator`` for the ``faq``, ``sales``, and ``human`` route
paths. See ``specs/workflow-services/spec.md``. Owner: Person B.
"""

from app.services.workflows.base import WorkflowTurnResult
from app.services.workflows.faq import FaqWorkflow
from app.services.workflows.human import HumanWorkflow
from app.services.workflows.sales import SalesWorkflow

__all__ = ["WorkflowTurnResult", "FaqWorkflow", "SalesWorkflow", "HumanWorkflow"]
