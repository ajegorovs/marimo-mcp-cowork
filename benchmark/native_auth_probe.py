"""Bounded native 0.25.1 auth-gated loopback probe.

Question
--------
With marimo's auth turned on (``--token``) and its default skew protection
left ON, does the native ``marimo pair`` CLI honour a throwaway credential
end to end on a disposable loopback notebook?

  A. No token at all            -> the kernel execute endpoint must refuse.
  B. Valid token via ``--token-file`` on a read-only request -> must succeed.
  C. A wrong token via ``--token-file`` -> must refuse.

Expected (from installed marimo 0.25.1 source)
----------------------------------------------
  - ``marimo edit … --token --token-password-file <f>`` starts an auth-gated
    server: ``GET /api/version`` answers 401 (process is up), no registry
    entry is written, and the session is opened via the documented
    ``access_token`` query parameter (``marimo._server.api.auth.TOKEN_QUERY_PARAM``)
    rather than a browser login.
  - A ``POST /api/kernel/execute`` without a valid ``Authorization: Bearer``
    header is answered 401 by ``AuthBackend``; the CLI maps 401/403 to
    ``PairError("Authentication failed.")`` -> exit 2 with
    ``{"success": false, "error": "Authentication failed.", "next": …}``.
  - The same request with the token read from ``--token-file`` (the token the
    server was booted with) resolves the session and executes read-only code.

Method (loopback only, no installation, bounded — one server run)
-----------------------------------------------------------------
1. Generate a throwaway credential with ``secrets``; write it to a private
   temp file OUTSIDE the repo (0600 on POSIX; Windows uses inherited ACLs).
   Never print its value.
2. Copy the committed fixture byte-identically into a disposable dir.
3. Boot one auth-gated server on the copy with DEFAULT skew protection (no
   ``--no-skew-protection``), using ``--token-password-file <private file>``.
4. Open one ``/sse`` session with ``access_token=<throwaway>``; wait for
   ``kernel-ready``. If the stream closes unauthorized instead, STOP with a
   blocker — never bypass the auth handshake.
5. Probe A: ``pair execute --session <SID>`` with no token -> assert exit 2,
   ``success: false``, auth wording, no stdout, marker absent.
6. Probe C: same, ``--token-file <wrong file>`` -> assert the same refusal.
7. Probe B: ``pair execute --file <copy> --token-file <private file>`` ->
   assert exit 0, ``success: true``, the read-only marker ran, and the
   resolved session id equals the live one.
8. Assert the repo fixture stayed byte-identical and neither throwaway token
   appears in captured session/client output.

Exit 0 with ``NATIVE_AUTH_PROBE_PASS``, or non-zero with
``NATIVE_AUTH_PROBE_BLOCKER`` / ``NATIVE_AUTH_PROBE_FAIL``.
The probe does not print credential values; it passes one as an `/sse` query
parameter, so server-side request logging is not ruled out. All probe state
is disposable.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import psutil

FIXTURE = Path(__file__).parent / "notebooks" / "mission_clean.py"
SCRATCH = Path(
    os.environ.get(
        "MARIMO_BENCHMARK_SCRATCH", Path.home() / ".cache" / "marimo-inspect"
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)

# A unique in-kernel marker; its absence proves the execute endpoint refused
# before any kernel code ran.
READ_ONLY_CODE = "print('PROBE_AUTH_READONLY_RAN')"
READ_ONLY_MARKER = "PROBE_AUTH_READONLY_RAN"

EXPECTED_CELLS = 5


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(proc: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    if proc.poll() is None:
        proc.terminate()
    psutil.wait_procs(children, timeout=5)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def server_status(url: str) -> int | None:
    """Return the HTTP status of ``/api/version`` (401 when auth is on)."""
    try:
        with urllib.request.urlopen(url + "/api/version", timeout=1) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return None


def write_private_token(path: Path, value: str) -> None:
    """Write *value* without a newline; directory access is OS-dependent."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)


def start_server(
    copy: Path, token_file: Path, env: dict
) -> tuple[subprocess.Popen, str]:
    """Boot one auth-gated headless server on loopback against *copy*.

    Auth is on (``--token``) with the throwaway credential in *token_file*,
    and skew protection is left at its default (ON) — no flag is passed.
    """
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            str(copy),
            "--headless",
            "--token",
            "--token-password-file",
            str(token_file),
            "--skip-update-check",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(150):
        if proc.poll() is not None:
            raise Blocker(f"server exited {proc.returncode}")
        if server_status(url) in (200, 401):
            return proc, url
        time.sleep(0.1)
    stop(proc)
    raise Blocker("server readiness timeout")


def open_session(url: str, session_id: str, file_path: Path, token: str) -> None:
    """Open one authenticated ``/sse`` session; stop if the handshake is denied.

    The documented ``access_token`` query parameter authenticates the GET
    (marimo's own ``TOKEN_QUERY_PARAM``); this is not a bypass. If the server
    answers with a ``close`` frame instead of ``kernel-ready``, raise a
    blocker carrying the sanitized close reason.
    """
    query = urllib.parse.urlencode(
        {
            "session_id": session_id,
            "file": str(file_path),
            "access_token": token,
        }
    )
    try:
        with urllib.request.urlopen(f"{url}/sse?{query}", timeout=30) as stream:
            awaiting_close_data = False
            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if "kernel-ready" in line:
                    return
                if line == "event: close":
                    awaiting_close_data = True
                    continue
                if awaiting_close_data and line.startswith("data: "):
                    raise Blocker("sse closed before kernel-ready")
    except urllib.error.HTTPError as error:
        raise Blocker(f"sse handshake refused: http {error.code}") from error
    except OSError as error:
        raise Blocker(f"sse handshake failed: {type(error).__name__}") from error
    raise Blocker("session did not reach kernel-ready")


def run_cli(
    url: str,
    *,
    session_id: str | None,
    file_path: Path | None,
    token_file: Path | None,
    code: str,
    env: dict,
) -> tuple[int, dict | None, str]:
    """Run native ``pair execute``; return (returncode, payload, raw stdout)."""
    argv = [sys.executable, "-m", "marimo", "pair", "execute", "--url", url]
    if session_id is not None:
        argv += ["--session", session_id]
    if file_path is not None:
        argv += ["--file", str(file_path)]
    if token_file is not None:
        argv += ["--token-file", str(token_file)]
    argv += ["-c", code]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = None
    return result.returncode, payload, result.stdout


def _denied(rc: int, payload: dict | None, raw: str, token: str, label: str) -> None:
    """Assert a run was refused for auth and no kernel code ran."""
    if rc == 0 or (payload or {}).get("success") is not False:
        raise AssertionError(
            f"{label}: request was ACCEPTED, auth not enforced "
            f"(exit={rc}, success={(payload or {}).get('success')!r})"
        )
    if payload is None:
        raise AssertionError(f"{label}: no JSON payload (exit={rc})")
    if "authentication" not in str(payload.get("error", "")).lower():
        raise AssertionError(f"{label}: refusal not auth-worded")
    if payload.get("stdout") is not None:
        raise AssertionError(f"{label}: refusal reported kernel stdout")
    if READ_ONLY_MARKER in raw:
        raise AssertionError(f"{label}: read-only marker appears in refused output")
    if token and token in raw:
        raise AssertionError(f"{label}: credential leaked in output")


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    # Throwaway per-probe credentials; never printed or logged.
    good_token = secrets.token_urlsafe(32)
    wrong_token = secrets.token_urlsafe(32)
    proc = None
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-auth-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_auth.py"
        copy.write_bytes(fixture_before)
        copy_before = copy.read_bytes()

        good_file = root / "token.good"
        wrong_file = root / "token.wrong"
        write_private_token(good_file, good_token)
        write_private_token(wrong_file, wrong_token)

        state = root / "state"
        state.mkdir()
        env = os.environ.copy()
        env["USERPROFILE"] = str(state)
        env["XDG_STATE_HOME"] = str(state)
        env.pop("MARIMO_TOKEN", None)

        try:
            proc, url = start_server(copy, good_file, env)
            if server_status(url) != 401:
                raise Blocker("auth-gated server did not answer 401 on /api/version")
            print("AUTH_SERVER_PID", proc.pid, "VERSION_STATUS", 401)

            sid = str(uuid.uuid4())
            open_session(url, sid, copy, good_token)
            print("SSE_SESSION_OPENED True", "SESSION_ID", sid)

            # Probe A: no token -> refuse.
            rc_a, payload_a, raw_a = run_cli(
                url,
                session_id=sid,
                file_path=None,
                token_file=None,
                code=READ_ONLY_CODE,
                env=env,
            )
            print(
                "NO_TOKEN",
                "exit",
                rc_a,
                "success",
                (payload_a or {}).get("success"),
                "auth_refusal",
                "authentication" in str((payload_a or {}).get("error", "")).lower(),
            )
            _denied(rc_a, payload_a, raw_a, good_token, "no-token")

            # Probe C: wrong token -> refuse.
            rc_c, payload_c, raw_c = run_cli(
                url,
                session_id=sid,
                file_path=None,
                token_file=wrong_file,
                code=READ_ONLY_CODE,
                env=env,
            )
            print(
                "WRONG_TOKEN",
                "exit",
                rc_c,
                "success",
                (payload_c or {}).get("success"),
                "auth_refusal",
                "authentication" in str((payload_c or {}).get("error", "")).lower(),
            )
            _denied(rc_c, payload_c, raw_c, wrong_token, "wrong-token")

            # Probe B: valid native --token-file read-only success.
            rc_b, payload_b, raw_b = run_cli(
                url,
                session_id=None,
                file_path=copy,
                token_file=good_file,
                code=READ_ONLY_CODE,
                env=env,
            )
            print(
                "VALID_TOKEN",
                "exit",
                rc_b,
                "success",
                (payload_b or {}).get("success"),
                "resolved_session",
                ((payload_b or {}).get("session") or {}).get("id"),
            )
            if rc_b != 0 or not payload_b or payload_b.get("success") is not True:
                raise AssertionError(
                    "valid-token read failed or returned unsuccessful payload"
                )
            if READ_ONLY_MARKER not in str(payload_b.get("stdout", "")):
                raise AssertionError("read-only marker missing from success output")
            if ((payload_b.get("session") or {}).get("id")) != sid:
                raise AssertionError("valid-token run resolved an unexpected session")
            if good_token in raw_b:
                raise AssertionError("credential leaked in success output")

            # Zero mutation of the committed fixture (the copy is disposable).
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("repo fixture was mutated")
            copy_unchanged = copy.read_bytes() == copy_before
            if not copy_unchanged:
                raise AssertionError(
                    "read-only authenticated probe changed disposable copy"
                )
            print("FIXTURE_UNCHANGED True")
            print("COPY_UNCHANGED True")
            print("NATIVE_AUTH_PROBE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_AUTH_PROBE_BLOCKER: {error}")
            return 3
        except AssertionError as error:
            print(f"NATIVE_AUTH_PROBE_FAIL: {error}")
            return 1
        finally:
            if proc is not None:
                stop(proc)


if __name__ == "__main__":
    sys.exit(main())
