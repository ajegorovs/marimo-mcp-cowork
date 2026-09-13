"""Evidence-based auth/scope denial classification (marimo 0.24).

Why the denied endpoint cannot classify itself
----------------------------------------------
Pinned marimo 0.24.0 turns an API 403 into ``401
{"detail":"Authorization header required"}`` and deliberately strips
``WWW-Authenticate`` from API responses. Measured rows (real 0.24.0 servers,
see ``docs/live-tests.md``):

```text
server            GET /api/sessions   GET /api/version   GET /
────────────────  ──────────────────  ─────────────────  ──────────────────────────────
run --no-token    401 auth-required   200 "0.24.0"       200 app shell (token marker)
edit/run --token  401 same body       401 same body      303 -> /auth/login?next=%2F
```

So a run-mode server whose *census* needs edit scope and a server that requires
marimo auth are byte-identical at the denied endpoint. Classifying from the
denied response alone — or from ``WWW-Authenticate``, or from a response byte
count — is therefore not implementable; it would confidently mislabel one case
as the other.

What this module does
---------------------
``classify_denied_census`` answers "was *this* call denied for lack of edit
scope, or is the server really auth-gated?" with one async read-scope probe and,
only when that probe is unavailable or ambiguous, the semantic markers of the
served landing page:

1. ``GET /api/version`` (read scope). 200 -> the server is reachable and
   readable without auth, so the denied call is an edit-scope denial.
2. The same endpoint denied with the **same** body as the denied call -> the
   server gates reads, so this is an auth gate.
3. Otherwise ambiguous -> ``GET /`` and its markers:
   ``<marimo-server-token data-token="…">`` means the app shell (edit scope
   missing), a ``/auth/login`` redirect or form means the login page (auth).
4. Still ambiguous -> ``session_census_denied``: a conservative "denied, cause
   undetermined" that never claims a certainty the probes did not establish.

Reason vocabulary
-----------------
``auth_required``
    The server requires authentication even for read-scope endpoints.
``edit_scope_required``
    The denied operation needs edit scope while a read-scope endpoint proves
    the server is otherwise reachable/readable (the run-mode case).
``session_census_denied``
    Conservative fallback: the follow-up probes could not separate auth from
    a scope/mode denial.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx2

from marimo_inspection.client import (
    MarimoClient,
    RootPageResponse,
    page_carries_skew_token,
)

#: Public denial-reason vocabulary shared by every target-resolving handler,
#: ``set_active_session`` and ``restart_kernel``. ``edit_scope_required`` is the
#: run-mode census denial; ``auth_required`` is a genuine auth gate.
REASON_AUTH_REQUIRED = "auth_required"
REASON_EDIT_SCOPE_REQUIRED = "edit_scope_required"
REASON_SESSION_CENSUS_DENIED = "session_census_denied"

#: The three reasons this classifier can return (handy for callers that branch).
ACCESS_REASONS = frozenset(
    {REASON_AUTH_REQUIRED, REASON_EDIT_SCOPE_REQUIRED, REASON_SESSION_CENSUS_DENIED}
)

#: Semantic page kinds, decided from markers only (never size or headers).
PAGE_APP_SHELL = "app_shell"
PAGE_LOGIN_PAGE = "login_page"
PAGE_UNKNOWN = "unknown"

#: The login-page marker. marimo answers ``GET /`` with a 303 to
#: ``/auth/login?next=…`` when auth is on, and the rendered login page carries a
#: form posting to the same path.
_LOGIN_MARKER = "/auth/login"


@dataclass(frozen=True)
class AccessProbe:
    """The classification of one denied census call, with its evidence.

    ``status_code`` is the denied endpoint's status (401/403);
    ``read_scope_status_code`` is what ``GET /api/version`` answered (``None``
    when there was no HTTP answer at all); ``page_kind`` is only meaningful when
    the page probe ran. ``detail`` explains the reasoning in prose for the
    refusal message.
    """

    reason: str
    status_code: int
    read_scope_status_code: int | None = None
    page_kind: str = PAGE_UNKNOWN
    detail: str = ""


def probe_evidence(probe: AccessProbe) -> dict[str, Any]:
    """The probe facts a refusal payload exposes alongside its reason.

    Kept in one place so ``resolve_target``, ``set_active_session`` and
    ``restart_kernel`` report the same evidence for the same classification.
    """
    return {
        "read_scope_status_code": probe.read_scope_status_code,
        "page_kind": probe.page_kind,
    }


def access_refusal_message(reason: str, url: str, *, detail: str = "") -> str:
    """The one prose form for an access denial, shared by every refusal path.

    ``detail`` is the classifier's own reasoning (which probe proved what), so a
    caller can see *why* the reason was chosen instead of only which reason it
    got. ``reason`` is one of this module's ``REASON_*`` constants.
    """
    tail = f" {detail}." if detail else ""
    if reason == REASON_EDIT_SCOPE_REQUIRED:
        return (
            f"reason: {REASON_EDIT_SCOPE_REQUIRED} — {url} denied the session "
            "census for lack of edit scope: the server is reachable without "
            "auth (a read-scope endpoint or the served app shell answered this "
            "unauthenticated client), but the census itself needs edit scope (a "
            "`marimo run` server is the typical case). Authenticating cannot fix "
            f"this — the server has to run in `edit` mode.{tail}"
        )
    if reason == REASON_AUTH_REQUIRED:
        return (
            f"reason: {REASON_AUTH_REQUIRED} — {url} requires marimo auth: it "
            "denies the session census and answers this unauthenticated client "
            "with an auth denial / a login page rather than a readable response, "
            f"so the server gates reads too.{tail}"
        )
    return (
        f"reason: {REASON_SESSION_CENSUS_DENIED} — {url} denied the session "
        "census (HTTP 401/403) and the follow-up read-scope probes could not "
        "tell a missing edit scope from an auth gate, so the cause is reported "
        f"as undetermined rather than guessed.{tail}"
    )


def access_next_steps(reason: str) -> list[str]:
    """Actionable recovery for each access denial, per reason."""
    if reason == REASON_EDIT_SCOPE_REQUIRED:
        return [
            (
                "Run the marimo server in `edit` mode (the documented headless "
                "recipe: `marimo edit <notebook> --no-token --headless`) so the "
                "session census is served, then retry."
            ),
            (
                "Use list_active_notebooks to confirm the server once it is in "
                "edit mode."
            ),
        ]
    if reason == REASON_AUTH_REQUIRED:
        return [
            (
                "Run the marimo server without auth (the documented headless "
                "recipe: `marimo edit <notebook> --no-token --headless`) so "
                "agent tooling can reach it, then retry."
            ),
            "Or restart the server manually if auth must stay on.",
        ]
    return [
        "Check the marimo server's auth and mode configuration, then retry.",
        "Use list_active_notebooks to see which servers answer.",
    ]


def http_error_body(exc: httpx2.HTTPError) -> str:
    """The denied response's body, for the same-denial comparison.

    Falls back to the exception text when no readable body is attached (a
    synthetic ``HTTPStatusError`` carries none), so the classifier never
    crashes on how an error was constructed.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        return response.text
    except (httpx2.HTTPError, UnicodeDecodeError):
        return str(exc)


def classify_page_kind(page: RootPageResponse) -> str:
    """Classify a served landing page from its semantic markers.

    An app shell carries marimo's skew-token element; a login page is either a
    redirect whose ``location`` names ``/auth/login`` (the measured 303) or a
    rendered form posting to it. Anything else — a 404, a plain page, a
    transport failure — is ``PAGE_UNKNOWN`` rather than a guess.
    """
    if page.transport_failed or not page.status_code:
        return PAGE_UNKNOWN
    if page.status_code == 200:
        if page_carries_skew_token(page.body):
            return PAGE_APP_SHELL
        if _LOGIN_MARKER in page.body:
            return PAGE_LOGIN_PAGE
        return PAGE_UNKNOWN
    if 300 <= page.status_code < 400 and _LOGIN_MARKER in page.location:
        return PAGE_LOGIN_PAGE
    return PAGE_UNKNOWN


def _normalized_detail(text: str) -> str:
    """The ``detail`` field of a marimo error body, or the trimmed text.

    marimo's API denials are JSON objects with a ``detail`` string; a non-JSON
    or non-object body is compared as its whitespace-collapsed text so two
    probes can still be compared for sameness.
    """
    text = text or ""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return " ".join(text.split())
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return " ".join(detail.split())
    return " ".join(text.split())


def _is_same_denial(read_scope_detail: str, denied_detail: str) -> bool:
    """True when both denials carry the same non-empty body.

    A read-scope endpoint denied with the *same* body as the denied call proves
    a blanket gate. A different body does not, so it stays ambiguous.
    """
    left = _normalized_detail(read_scope_detail)
    right = _normalized_detail(denied_detail)
    return bool(left) and left == right


async def classify_denied_census(
    client: MarimoClient,
    server_url: str,
    *,
    denied_status: int,
    denied_detail: str = "",
) -> AccessProbe:
    """Classify a 401/403 denial of a session census / edit-scope call.

    Read-only: two probes at most, nothing written, and no reliance on
    ``WWW-Authenticate`` or response byte size.

    Args:
        client: A client for the denied server (still open; the caller closes).
        server_url: The server's base URL, for the evidence detail.
        denied_status: The status the denied endpoint answered (401/403).
        denied_detail: The denied response body, for same-denial comparison.

    Returns:
        An ``AccessProbe`` whose ``reason`` is ``edit_scope_required``,
        ``auth_required`` or ``session_census_denied``.
    """
    version = await client.probe_version()
    if version.status_code == 200:
        return AccessProbe(
            reason=REASON_EDIT_SCOPE_REQUIRED,
            status_code=denied_status,
            read_scope_status_code=200,
            detail=(
                f"GET /api/version at {server_url} answered 200, so the server "
                "is reachable and readable without auth; the denied endpoint "
                f"(HTTP {denied_status}) requires edit scope — the run-mode case"
            ),
        )

    if version.status_code in (401, 403) and _is_same_denial(
        version.detail, denied_detail
    ):
        return AccessProbe(
            reason=REASON_AUTH_REQUIRED,
            status_code=denied_status,
            read_scope_status_code=version.status_code,
            detail=(
                f"GET /api/version at {server_url} answered "
                f"{version.status_code} with the same auth body as the denied "
                "call, so the server gates reads — marimo auth is enabled"
            ),
        )

    page = await client.probe_root_page()
    page_kind = classify_page_kind(page)
    if page_kind == PAGE_APP_SHELL:
        return AccessProbe(
            reason=REASON_EDIT_SCOPE_REQUIRED,
            status_code=denied_status,
            read_scope_status_code=version.status_code or None,
            page_kind=page_kind,
            detail=(
                f"the read-scope probe at {server_url} did not prove auth, but "
                "GET / served the app shell (the skew-token marker is present), "
                "so the denied call is an edit-scope denial"
            ),
        )
    if page_kind == PAGE_LOGIN_PAGE:
        return AccessProbe(
            reason=REASON_AUTH_REQUIRED,
            status_code=denied_status,
            read_scope_status_code=version.status_code or None,
            page_kind=page_kind,
            detail=(
                f"the read-scope probe at {server_url} was inconclusive, but "
                "GET / served the login page (no skew-token marker, "
                "/auth/login present), so the server requires auth"
            ),
        )

    return AccessProbe(
        reason=REASON_SESSION_CENSUS_DENIED,
        status_code=denied_status,
        read_scope_status_code=version.status_code or None,
        page_kind=page_kind,
        detail=(
            f"the census at {server_url} was denied (HTTP {denied_status}) and "
            "the follow-up probes could not tell a missing edit scope from an "
            "auth gate, so the cause is reported as undetermined rather than "
            "guessed"
        ),
    )
