"""The scheduler's ping endpoint.

An external monitor (UptimeRobot, a GitHub Action, anything that can hit a
URL on a timer) calls this every few minutes. Each call starts one bounded
pass of the scheduler in the background and returns immediately, so the
monitor never times out and the site never blocks.

Protected by SCHEDULER_TOKEN: without it configured the endpoint refuses
outright, and a wrong token is a plain 403. The token can be sent as an
`X-Scheduler-Token` header, a bearer token, or - for monitors that can't
set headers - a `token` query parameter.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .scheduler import is_running, last_run, start_background_run


def _supplied_token(request) -> str:
    header = request.headers.get("X-Scheduler-Token", "")
    if header:
        return header
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.GET.get("token", "")


@csrf_exempt
@require_http_methods(["GET", "HEAD", "POST"])
def run_scheduled(request):
    expected = settings.SCHEDULER_TOKEN
    if not expected:
        return JsonResponse(
            {"error": "SCHEDULER_TOKEN is not set, so the scheduler cannot be pinged."},
            status=503)
    supplied = _supplied_token(request)
    if not supplied or not hmac.compare_digest(supplied, expected):
        return JsonResponse({"error": "forbidden"}, status=403)

    if request.method == "HEAD":
        return HttpResponse(status=200)

    started = start_background_run(settings.SCHEDULER_BUDGET_SECONDS)
    previous = last_run()
    return JsonResponse({
        "started": started,
        "running": is_running(),
        "last_run": previous and {
            "at": previous["at"].isoformat(), "seconds": previous["seconds"],
            "lines": previous["lines"],
        },
    }, status=202 if started else 200)
