"""Serves the embeddable widget bundle at ``GET /widget.js``.

The Admin panel's embed snippet (``admin_app/pages/embed_snippet.py``) generates
HTML of the form::

    <script src="{api_origin}/widget.js" data-widget-id="…" async></script>

That is the *only* contract this module exists to honor. The bundle itself is
produced by the ``widget/`` package during the API image's multi-stage build
(see ``backend/Dockerfile``) and copied to ``/app/static/widget.js`` inside the
runtime image.

The route is intentionally tiny:

* No tenant scoping — the bundle is a static asset identical for every tenant.
  Tenant identity travels via the ``data-widget-id`` attribute and is resolved
  server-side by ``POST /public/widgets/session`` at runtime.
* No auth — same reasoning. The asset is meant to be loaded by anonymous
  visitors on customer websites.
* Long-cached. ``Cache-Control: public, max-age=300`` (5 minutes) is a
  conservative default that survives single-tenant deploys while letting
  bug-fix rebuilds propagate quickly. Tune via the ``WIDGET_BUNDLE_MAX_AGE``
  environment variable if needed downstream.

Returns 404 with a descriptive message if the bundle is missing (build
misconfiguration) instead of a generic FastAPI not-found, so the failure mode
is easy to spot from a curl probe during smoke tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["widget-asset"])

_BUNDLE_PATH = Path(os.environ.get("WIDGET_BUNDLE_PATH", "/app/static/widget.js"))
_MAX_AGE = int(os.environ.get("WIDGET_BUNDLE_MAX_AGE", "300"))


@router.get(
    "/widget.js",
    summary="Embeddable chat widget bundle (static JS)",
    include_in_schema=False,
)
async def get_widget_bundle() -> FileResponse:
    if not _BUNDLE_PATH.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"widget bundle not found at {_BUNDLE_PATH} — "
                "verify the multi-stage build copied dist/widget.js into the image"
            ),
        )
    return FileResponse(
        path=_BUNDLE_PATH,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": f"public, max-age={_MAX_AGE}"},
    )
