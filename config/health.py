"""Health check.

Answers one question: can this instance serve a request that touches the
database? That is the failure mode worth alerting on - the app process can
be up while the database is unreachable, and from the outside those look
identical until someone tries to sign up.

Deliberately does *not* report on SendGrid or OpenAI. Both degrade
gracefully by design (a missing key means templates and dry runs, not an
outage), so failing the health check on them would take the site out of
rotation over something that isn't stopping anyone using it. The admin
dashboard shows their state instead.

Deliberately terse in what it returns. This is a public URL, so it reports
whether things work, never why - an exception message can carry a
connection string or a host name.
"""

from __future__ import annotations

import logging
import time

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


@never_cache
def healthz(request):
    """200 when the instance can serve; 503 when it can't."""
    checks = {}
    healthy = True

    started = time.monotonic()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:
        # Logged in full, reported as a single word - see the module note.
        logger.exception("Health check: database unreachable")
        checks["database"] = "error"
        healthy = False
    checks["database_ms"] = round((time.monotonic() - started) * 1000)

    payload = {"status": "ok" if healthy else "unhealthy", **checks}
    return JsonResponse(payload, status=200 if healthy else 503)
