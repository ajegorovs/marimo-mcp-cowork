"""Evidence-based auth/scope denial classification (Task 01).

Why a denied census cannot classify itself
------------------------------------------
Measured against real marimo 0.24.0 servers (see ``docs/live-tests.md``):

```text
server                 GET /api/sessions                            GET /api/version                    GET /
─────────────────────  ───────────────────────────────────────────  ──────────────────────────────────  ────────────────────────
run --no-token         401 {"detail":"Authorization header required"} 200 "0.24.0"                        200 app shell (token marker)
run/edit --token       401 same body                                  401 same body                       303 -> /auth/login?next=%2F
```

marimo converts an API 403 into that 401 body and strips ``WWW-Authenticate``,
so a run-mode scope denial and a true auth gate are byte-identical at the denied
endpoint. The classifier therefore probes a **read-scope** endpoint
(`GET /api/version`) and falls back to the *semantic* markers of `GET /` —
never the response header, never the response byte size.

Rows pinned here:

* denied census + ``/api/version`` 200 -> ``edit_scope_required`` (run mode);
* denied census + ``/api/version`` 401 with the **same** auth body ->
  ``auth_required``;
* denied census + an ambiguous/unavailable read-scope probe + app-shell page ->
  ``edit_scope_required``; + login page -> ``auth_required``;
* ambiguous on both probes -> ``session_census_denied`` (conservative: never a
  confident wrong answer).

The classifier runs on a fake client (its own branch logic) and, for the probe
primitives, through the real ``MarimoClient`` on an ``httpx2.MockTransport``.
"""

from __future__ import annotations

import httpx2
import pytest

from marimo_inspection.client import (
    MarimoClient,
    ProbeResponse,
    RootPageResponse,
    page_carries_skew_token,
)
from marimo_inspection.tools.access import (
    PAGE_APP_SHELL,
    PAGE_LOGIN_PAGE,
    PAGE_UNKNOWN,
    REASON_AUTH_REQUIRED,
    REASON_EDIT_SCOPE_REQUIRED,
    REASON_SESSION_CENSUS_DENIED,
    AccessProbe,
    classify_denied_census,
    classify_page_kind,
    probe_evidence,
)

#: The exact body real marimo 0.24.0 answers for both an auth gate and an API
#: 403 (edit-scope) denial.
AUTH_BODY = '{"detail":"Authorization header required"}'

#: A served app shell: the skew-token marker element proves the page is the app.
APP_SHELL_BODY = '<html><body><marimo-server-token data-token="tok" hidden></html>'

#: A served login page: no token marker, a form posting to /auth/login (the
#: measured auth-on response once its 303 is followed).
LOGIN_BODY = (
    '<html><body><form method="post" action="/auth/login"></form></body></html>'
)

#: The observed auth redirect for `GET /` on an auth-gated server.
LOGIN_LOCATION = "/auth/login?next=%2F"


class FakeProbeClient:
    """Scripted probe seam: no transport, only the two probe methods."""

    def __init__(
        self,
        version: ProbeResponse | None = None,
        page: RootPageResponse | None = None,
    ) -> None:
        self._version = version if version is not None else ProbeResponse(200, "0.24.0")
        self._page = (
            page
            if page is not None
            else RootPageResponse(status_code=200, body=APP_SHELL_BODY)
        )
        self.version_calls = 0
        self.page_calls = 0

    async def probe_version(self) -> ProbeResponse:
        self.version_calls += 1
        return self._version

    async def probe_root_page(self) -> RootPageResponse:
        self.page_calls += 1
        return self._page


# ---------------------------------------------------------------------------
# 1. The primary discriminator: a readable read-scope endpoint.
# ---------------------------------------------------------------------------


async def test_readable_read_scope_probe_means_edit_scope_required():
    """A run-mode denial: the census needs edit scope, the server is readable."""
    client = FakeProbeClient()

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_EDIT_SCOPE_REQUIRED
    assert probe.status_code == 401
    assert probe.read_scope_status_code == 200
    # The read-scope answer already decides: the page is never fetched.
    assert client.page_calls == 0


@pytest.mark.parametrize("denied_status", [401, 403], ids=["401", "403"])
async def test_readable_read_scope_probe_wins_for_either_denied_status(denied_status):
    """A literal 403 is classified the same way (defensive; 0.24 serves 401)."""
    probe = await classify_denied_census(
        FakeProbeClient(),
        "http://127.0.0.1:8090",
        denied_status=denied_status,
        denied_detail=AUTH_BODY,
    )

    assert probe.reason == REASON_EDIT_SCOPE_REQUIRED
    assert probe.status_code == denied_status
    assert probe.read_scope_status_code == 200


# ---------------------------------------------------------------------------
# 2. A read-scope probe refused with the same auth body -> true auth gate.
# ---------------------------------------------------------------------------


async def test_denied_read_scope_probe_with_same_body_means_auth_required():
    client = FakeProbeClient(version=ProbeResponse(401, AUTH_BODY))

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_AUTH_REQUIRED
    assert probe.read_scope_status_code == 401
    assert probe.page_kind == PAGE_UNKNOWN
    assert client.page_calls == 0


# ---------------------------------------------------------------------------
# 3. An ambiguous read-scope probe: the semantic page markers decide.
# ---------------------------------------------------------------------------


async def test_ambiguous_probe_with_app_shell_page_means_edit_scope_required():
    client = FakeProbeClient(
        version=ProbeResponse(404, "not found"),
        page=RootPageResponse(status_code=200, body=APP_SHELL_BODY),
    )

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_EDIT_SCOPE_REQUIRED
    assert probe.page_kind == PAGE_APP_SHELL
    assert probe.read_scope_status_code == 404
    assert client.page_calls == 1


async def test_ambiguous_probe_with_login_page_means_auth_required():
    client = FakeProbeClient(
        version=ProbeResponse(404, "not found"),
        page=RootPageResponse(status_code=303, location=LOGIN_LOCATION),
    )

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_AUTH_REQUIRED
    assert probe.page_kind == PAGE_LOGIN_PAGE


async def test_denied_probe_with_a_different_body_is_not_read_as_auth():
    """A 401 with a body unlike the denied one is ambiguous, not "auth"."""
    client = FakeProbeClient(
        version=ProbeResponse(401, '{"detail":"Nope"}'),
        page=RootPageResponse(status_code=200, body=LOGIN_BODY),
    )

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_AUTH_REQUIRED
    assert probe.read_scope_status_code == 401
    # ... and it was the *page* that decided, not the version body.
    assert client.page_calls == 1


# ---------------------------------------------------------------------------
# 4. Ambiguous on both probes -> the conservative reason, never a guess.
# ---------------------------------------------------------------------------


async def test_ambiguous_on_both_probes_is_session_census_denied():
    client = FakeProbeClient(
        version=ProbeResponse(404, "not found"),
        page=RootPageResponse(status_code=404, body="stub: no such endpoint"),
    )

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_SESSION_CENSUS_DENIED
    assert probe.page_kind == PAGE_UNKNOWN
    assert probe.read_scope_status_code == 404


async def test_transport_failures_end_in_the_conservative_reason():
    client = FakeProbeClient(
        version=ProbeResponse(transport_failed=True, detail="connection refused"),
        page=RootPageResponse(transport_failed=True, detail="connection refused"),
    )

    probe = await classify_denied_census(
        client, "http://127.0.0.1:8090", denied_status=401, denied_detail=AUTH_BODY
    )

    assert probe.reason == REASON_SESSION_CENSUS_DENIED
    assert probe.read_scope_status_code is None
    assert probe.page_kind == PAGE_UNKNOWN


async def test_no_denied_detail_still_prefers_the_read_scope_evidence():
    """Without a body to compare, a 200 read-scope probe still decides."""
    probe = await classify_denied_census(
        FakeProbeClient(), "http://127.0.0.1:8090", denied_status=401
    )

    assert probe.reason == REASON_EDIT_SCOPE_REQUIRED


# ---------------------------------------------------------------------------
# 5. Page classification is semantic — never a byte-size or header heuristic.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        pytest.param(
            RootPageResponse(status_code=200, body=APP_SHELL_BODY),
            PAGE_APP_SHELL,
            id="app-shell-marker",
        ),
        pytest.param(
            RootPageResponse(status_code=200, body=LOGIN_BODY),
            PAGE_LOGIN_PAGE,
            id="rendered-login-form",
        ),
        pytest.param(
            RootPageResponse(status_code=303, location=LOGIN_LOCATION),
            PAGE_LOGIN_PAGE,
            id="auth-redirect",
        ),
        pytest.param(
            RootPageResponse(status_code=200, body="<html>plain</html>"),
            PAGE_UNKNOWN,
            id="plain-page",
        ),
        pytest.param(
            RootPageResponse(status_code=303, location="/elsewhere"),
            PAGE_UNKNOWN,
            id="unrelated-redirect",
        ),
        pytest.param(
            RootPageResponse(status_code=404, body="nope"),
            PAGE_UNKNOWN,
            id="missing-page",
        ),
        pytest.param(
            RootPageResponse(transport_failed=True, detail="refused"),
            PAGE_UNKNOWN,
            id="transport-failure",
        ),
    ],
)
def test_page_kind_comes_from_semantic_markers(page, expected):
    assert classify_page_kind(page) == expected


def test_page_kind_is_independent_of_body_size():
    """A huge login page and a tiny app shell classify by marker, not length."""
    huge_login = LOGIN_BODY + ("<!-- filler -->" * 2000)
    tiny_shell = '<marimo-server-token data-token="t" hidden>'

    assert len(huge_login) > 10000 > len(tiny_shell)
    assert classify_page_kind(RootPageResponse(200, body=huge_login)) == PAGE_LOGIN_PAGE
    assert classify_page_kind(RootPageResponse(200, body=tiny_shell)) == PAGE_APP_SHELL


def test_probe_evidence_carries_the_two_probe_facts():
    """The payload fields a refusal exposes are exactly the probe evidence."""
    probe = AccessProbe(
        reason=REASON_EDIT_SCOPE_REQUIRED,
        status_code=401,
        read_scope_status_code=200,
        page_kind=PAGE_APP_SHELL,
        detail="detail",
    )

    assert probe_evidence(probe) == {
        "read_scope_status_code": 200,
        "page_kind": PAGE_APP_SHELL,
    }


# ---------------------------------------------------------------------------
# 6. The probe primitives, through the real client on a MockTransport.
# ---------------------------------------------------------------------------

PROBE_URL = "http://stub"


def _client(handler) -> MarimoClient:
    client = MarimoClient(PROBE_URL)
    client._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return client


async def test_probe_version_reads_status_and_body():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/version"
        return httpx2.Response(401, text=AUTH_BODY)

    client = _client(handler)
    try:
        probe = await client.probe_version()
    finally:
        await client.close()

    assert probe.status_code == 401
    assert probe.transport_failed is False
    assert "Authorization header required" in probe.detail


async def test_probe_version_reports_a_transport_failure_without_raising():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    client = _client(handler)
    try:
        probe = await client.probe_version()
    finally:
        await client.close()

    assert probe.status_code == 0
    assert probe.transport_failed is True
    assert probe.detail


async def test_probe_root_page_does_not_follow_the_auth_redirect():
    """The redirect itself is the evidence; following it hides the marker."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return httpx2.Response(303, headers={"location": LOGIN_LOCATION})

    client = _client(handler)
    try:
        page = await client.probe_root_page()
    finally:
        await client.close()

    assert seen == ["/"]
    assert page.status_code == 303
    assert page.location == LOGIN_LOCATION
    assert page.body == ""
    assert classify_page_kind(page) == PAGE_LOGIN_PAGE


async def test_probe_root_page_carries_the_app_shell_marker():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=APP_SHELL_BODY)

    client = _client(handler)
    try:
        page = await client.probe_root_page()
    finally:
        await client.close()

    assert page.status_code == 200
    assert page_carries_skew_token(page.body) is True
    assert classify_page_kind(page) == PAGE_APP_SHELL


def test_page_carries_skew_token_requires_the_marker_element():
    assert page_carries_skew_token(APP_SHELL_BODY) is True
    assert page_carries_skew_token("<html>no marker</html>") is False
    assert page_carries_skew_token("") is False
