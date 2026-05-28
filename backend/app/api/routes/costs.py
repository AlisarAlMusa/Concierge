# Cost routes — placeholder. Usage-summary endpoints live in admin_config.py
# (GET /tenant/usage-summary) and tenants.py (GET /platform/tenants/{id}/usage-summary).
from fastapi import APIRouter

router = APIRouter(tags=["costs"])
