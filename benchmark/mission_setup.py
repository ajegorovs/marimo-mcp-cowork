"""Arm setup for the restricted native-vs-wrapper mission comparison.

Stages ONE disposable arm: a byte-exact copy of the canonical fixture, a single
headless loopback marimo server, and the single session that server serves. The
program is argument-driven and machine-agnostic — the canonical fixture, the
disposable root, the interpreter, the state dir and the manifest target all come
from the caller, and the notebook a server opens is *derived* from
``--canonical``. The CLI accepts no target selector, so a caller cannot point an
arm at a notebook it did not stage itself.

Design seams
------------
* :func:`perform_setup` holds the whole orchestration and takes an injectable
  ``server_factory``. The live :class:`ServerSession` is only the default
  implementation, so the pure logic runs in tests with no server at all.
* :func:`stage_disposable` copies with ``read_bytes`` / ``write_bytes`` (never a
  text round-trip, which would translate line endings on Windows) and delegates
  byte-identity, root-containment and non-alias checks to :mod:`session_guard`.
* The manifest is plain JSON and records nothing but the run's coordinates.
  The arm reads it, so it is *not* an anchor an arm cannot move: an arm that
  can run arbitrary in-kernel code plus a shell can rewrite it. ``--control-dir``
  therefore writes a second, byte-level copy of the same identity into a
  directory the arm is never pointed at (``anchor.json``: the identity fields
  plus the SHA-256 of the manifest file's bytes), and the grader refuses unless
  the control copy still pins the manifest. Writing that copy is not prevention
  either — it is detection, and it only works when the caller places the control
  dir somewhere the arm cannot write. That placement is the caller's
  responsibility; this module cannot enforce it.

Scope honesty: this stages and records a disposable arm; it does not *pin* the
server it started. The identity checks are detection of a captured snapshot, not
atomic prevention (see ``session_guard``). On success the server is deliberately
left running — the mission runs against the live session — while a failed stage
stops it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ARMS",
    "CONTROL_FILENAME",
    "CONTROL_IDENTITY_KEYS",
    "CONTROL_KEYS",
    "MANIFEST_KEYS",
    "ServerSession",
    "SetupRefusal",
    "build_control_anchor",
    "build_manifest",
    "build_server_command",
    "copy_bytes",
    "disposable_digest",
    "main",
    "perform_setup",
    "pick_free_port",
    "stage_disposable",
    "utc_now",
    "write_control_anchor",
    "write_manifest",
]

ARMS = ("native", "wrapper")

# The manifest keys a grader may rely on. ``canonical_path``, ``root`` and
# ``state_dir`` are recorded alongside them so a grader can re-hash the fixture
# and isolate its own fresh boot without reading any other channel.
MANIFEST_KEYS = (
    "server_url",
    "session_id",
    "notebook_path",
    "process_id",
    "python",
    "sha256",
    "arm",
    "generated_at",
)

# The control copy: the file name inside ``--control-dir`` and the identity
# fields it pins. A grader compares every identity field against the manifest,
# plus ``manifest_sha256`` against the manifest FILE's bytes — so a rewritten
# manifest fails even when its identity fields were left alone.
CONTROL_FILENAME = "anchor.json"
CONTROL_IDENTITY_KEYS = (
    "server_url",
    "session_id",
    "notebook_path",
    "process_id",
    "sha256",
    "arm",
)
CONTROL_KEYS = (*CONTROL_IDENTITY_KEYS, "manifest_sha256")


class SetupRefusal(RuntimeError):
    """The arm could not be staged fail-closed; the caller must not use it."""


def _load_sibling(name: str, filename: str):
    """Load a sibling module by path, cached in ``sys.modules`` under *name*.

    Avoids mutating ``sys.path`` and works whether this file is imported as a
    script or loaded via ``importlib`` from a test.
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / filename
    )
    if spec is None or spec.loader is None:
        raise SetupRefusal(f"cannot load sibling module {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _guard():
    """The pure session guards (``benchmark/session_guard.py``)."""
    return _load_sibling("_mission_session_guard", "session_guard.py")


def utc_now() -> str:
    """The manifest's ``generated_at`` value: an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def pick_free_port() -> int:
    """Return a free loopback port (bind to port 0, read the assignment)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_server_command(
    python: str, notebook: str | os.PathLike[str], port: int
) -> list[str]:
    """The headless loopback ``marimo edit`` argv for one disposable arm."""
    return [
        str(python),
        "-m",
        "marimo",
        "edit",
        str(notebook),
        "--headless",
        "--no-token",
        "--no-skew-protection",
        "--skip-update-check",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def copy_bytes(source: str | os.PathLike[str], dest: str | os.PathLike[str]) -> Path:
    """Copy *source* to *dest* byte-for-byte (never a text round-trip)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(source).read_bytes())
    return dest


def disposable_digest(
    canonical: str | os.PathLike[str],
    copies: list[str | os.PathLike[str]],
    root: str | os.PathLike[str],
) -> str:
    """Fail-closed byte-identity / containment check via :mod:`session_guard`.

    Returns the canonical notebook's SHA-256 hex digest and raises
    :class:`ValueError` (from the guard) if a copy is missing, differs, escapes
    the disposable root, or aliases another file.
    """
    return _guard().validate_disposable(
        Path(canonical), [Path(copy) for copy in copies], Path(root)
    )


def stage_disposable(
    canonical: str | os.PathLike[str],
    root: str | os.PathLike[str],
    *,
    name: str = "notebook.py",
) -> tuple[Path, str]:
    """Copy the canonical into *root* byte-exactly and enforce the guard.

    Returns ``(copy_path, canonical_sha256)``.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    copy = copy_bytes(canonical, root / name)
    digest = disposable_digest(canonical, [copy], root)
    return copy, digest


def build_manifest(
    *,
    server_url: str,
    session_id: str,
    notebook_path: str,
    process_id: int,
    python: str,
    sha256: str,
    arm: str,
    generated_at: str,
    canonical_path: str,
    root: str,
    state_dir: str,
) -> dict:
    """Assemble and validate the manifest mapping for one staged arm."""
    if arm not in ARMS:
        raise SetupRefusal(f"arm must be one of {ARMS}, got {arm!r}")
    text_fields = (
        ("server_url", server_url),
        ("session_id", session_id),
        ("notebook_path", notebook_path),
        ("python", python),
        ("sha256", sha256),
        ("generated_at", generated_at),
        ("canonical_path", canonical_path),
        ("root", root),
        ("state_dir", state_dir),
    )
    for field, value in text_fields:
        if not isinstance(value, str) or not value.strip():
            raise SetupRefusal(f"manifest field {field} must be a non-empty string")
    return {
        "server_url": server_url,
        "session_id": session_id,
        "notebook_path": notebook_path,
        "process_id": int(process_id),
        "python": python,
        "sha256": sha256,
        "arm": arm,
        "generated_at": generated_at,
        "canonical_path": canonical_path,
        "root": root,
        "state_dir": state_dir,
    }


def write_manifest(path: str | os.PathLike[str], manifest: dict) -> None:
    """Write *manifest* as UTF-8 JSON with LF newlines, deterministically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_control_anchor(manifest: dict, manifest_sha256: str) -> dict:
    """Assemble the control copy for *manifest*: identity + the manifest digest.

    ``notebook_path`` is recorded **resolved**: the control copy pins the file
    the arm was staged against, not the spelling the manifest happened to use.

    Raises:
        SetupRefusal: If the manifest carries no non-empty identity field or no
            usable ``manifest_sha256``.
    """
    missing = [key for key in CONTROL_IDENTITY_KEYS if key not in manifest]
    if missing:
        raise SetupRefusal(f"manifest carries no {missing} for the control copy")
    anchor = {key: manifest[key] for key in CONTROL_IDENTITY_KEYS}
    anchor["notebook_path"] = str(Path(str(manifest["notebook_path"])).resolve())
    try:
        anchor["process_id"] = int(manifest["process_id"])
    except (TypeError, ValueError) as exc:
        raise SetupRefusal(
            f"manifest process_id is not an int: {manifest['process_id']!r}"
        ) from exc
    if not isinstance(manifest_sha256, str) or not manifest_sha256.strip():
        raise SetupRefusal("control copy needs a non-empty manifest sha256")
    anchor["manifest_sha256"] = manifest_sha256
    return anchor


def write_control_anchor(
    control_dir: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
) -> Path:
    """Write ``anchor.json`` into *control_dir*, pinned to the manifest bytes.

    The copy is derived from the manifest **file** — its bytes are hashed and its
    identity fields are re-read from disk — so the control copy and the manifest
    cannot disagree by construction, and any later rewrite of the manifest is
    detectable byte-for-byte. The caller must place *control_dir* where the arm
    cannot write it; that placement is not enforceable here.

    Returns:
        The path of the written control copy.

    Raises:
        SetupRefusal: If *control_dir* exists but is not a directory, the
            manifest cannot be re-read, or the manifest lacks an identity field.
    """
    control_dir = Path(control_dir)
    if control_dir.exists() and not control_dir.is_dir():
        raise SetupRefusal(f"control dir exists but is not a directory: {control_dir}")
    control_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(manifest_path)
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SetupRefusal(
            f"cannot re-read the manifest for the control copy: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise SetupRefusal(f"manifest must be a JSON object: {manifest_path}")
    anchor = build_control_anchor(manifest, hashlib.sha256(raw).hexdigest())
    path = control_dir / CONTROL_FILENAME
    path.write_text(
        json.dumps(anchor, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _safe_stop(server: object) -> None:
    """Best-effort stop of a staged server, swallowing teardown errors."""
    stop = getattr(server, "stop", None)
    if stop is None:
        return
    with contextlib.suppress(Exception):
        stop()


def perform_setup(
    canonical: str | os.PathLike[str],
    root: str | os.PathLike[str],
    python: str,
    arm: str,
    state_dir: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    *,
    server_factory: Callable[[Path, str, Path], object],
    clock: Callable[[], str] | None = None,
    control_dir: str | os.PathLike[str] | None = None,
) -> dict:
    """Stage one arm and write its manifest; leave the server running.

    Steps: create the disposable root, stage a byte-exact copy guarded by
    :func:`disposable_digest`, boot ONE loopback server (via *server_factory*),
    create ONE session, read the census, and enforce
    :func:`session_guard.validate_census`. Any failure stops the staged server
    and raises :class:`SetupRefusal`; on success the server is left running and
    the manifest is written.

    Args:
        canonical: The pristine fixture, kept outside the disposable root.
        root: The disposable workspace directory (must be empty or absent).
        python: Interpreter used to boot the server.
        arm: One of :data:`ARMS`.
        state_dir: Isolated ``USERPROFILE`` / ``XDG_STATE_HOME`` for the server.
        manifest_path: Where the JSON manifest is written.
        server_factory: ``(notebook, python, state_dir) -> server``; the
            injectable seam. The returned object must expose ``start()``,
            ``create_session()``, ``census()``, ``stop()`` and the
            ``server_url`` / ``session_id`` / ``process_id`` attributes.
        clock: Optional ``() -> str`` timestamp source for deterministic tests.
        control_dir: When given, a second copy of the manifest's identity —
            ``anchor.json`` (identity fields + the SHA-256 of the manifest
            file's bytes) — is written there. The arm is never pointed at it;
            the caller must place it where the arm cannot write. The manifest
            itself is unchanged.

    Returns:
        The manifest mapping that was written.

    Raises:
        SetupRefusal: If any precondition or guard fails.
    """
    if arm not in ARMS:
        raise SetupRefusal(f"arm must be one of {ARMS}, got {arm!r}")
    canonical = Path(canonical)
    root = Path(root)
    state_dir = Path(state_dir)

    if not canonical.is_file():
        raise SetupRefusal(f"canonical fixture does not exist: {canonical}")
    if root.exists() and not root.is_dir():
        raise SetupRefusal(f"disposable root exists but is not a directory: {root}")
    if root.exists() and any(root.iterdir()):
        raise SetupRefusal(f"disposable root is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    copy, digest = stage_disposable(canonical, root)

    server = server_factory(copy, str(python), state_dir)
    try:
        server.start()
        session_id = server.create_session()
        census = server.census()
        guard = _guard()
        anchor = guard.SessionAnchor(session_id, str(copy), int(server.process_id))
        guard.validate_census(census, anchor)
    except SetupRefusal:
        _safe_stop(server)
        raise
    except Exception as exc:
        _safe_stop(server)
        raise SetupRefusal(f"arm staging failed: {exc}") from exc

    manifest = build_manifest(
        server_url=str(server.server_url),
        session_id=session_id,
        notebook_path=str(copy),
        process_id=int(server.process_id),
        python=str(python),
        sha256=digest,
        arm=arm,
        generated_at=(clock or utc_now)(),
        canonical_path=str(canonical),
        root=str(root),
        state_dir=str(state_dir),
    )
    try:
        write_manifest(manifest_path, manifest)
        if control_dir is not None:
            write_control_anchor(control_dir, manifest_path)
    except SetupRefusal:
        # A staged-but-unrecorded arm is unusable: stop the server it started.
        _safe_stop(server)
        raise
    except OSError as exc:
        _safe_stop(server)
        raise SetupRefusal(f"cannot record the arm: {exc}") from exc
    return manifest


class ServerSession:
    """One headless loopback marimo server and the single session it serves.

    This is the default implementation of the injectable seam consumed by
    :func:`perform_setup`; a test substitutes any object of the same shape and
    no process is launched.
    """

    READY_TIMEOUT_S = 30.0
    HANDLE_TIMEOUT_S = 15.0
    CENSUS_TIMEOUT_S = 3.0
    POLL_INTERVAL_S = 0.1

    def __init__(
        self,
        notebook: str | os.PathLike[str],
        python: str,
        state_dir: str | os.PathLike[str],
    ):
        self.notebook = Path(notebook)
        self.python = str(python)
        self.state_dir = Path(state_dir)
        self.server_url: str | None = None
        self.session_id: str | None = None
        self.process: subprocess.Popen | None = None

    @property
    def process_id(self) -> int:
        """The running server's process id (raises before ``start()``)."""
        if self.process is None:
            raise SetupRefusal("server is not running")
        return int(self.process.pid)

    def _env(self) -> dict:
        env = dict(os.environ)
        env["USERPROFILE"] = str(self.state_dir)
        env["XDG_STATE_HOME"] = str(self.state_dir)
        return env

    def start(self) -> str:
        """Boot the server and wait until ``/api/version`` answers 200."""
        port = pick_free_port()
        self.process = subprocess.Popen(
            build_server_command(self.python, self.notebook, port),
            env=self._env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SetupRefusal(
                    f"server exited early with {self.process.returncode}"
                )
            try:
                with urllib.request.urlopen(
                    url + "/api/version", timeout=1
                ) as response:
                    if response.status == 200:
                        self.server_url = url
                        return url
            except OSError:
                time.sleep(self.POLL_INTERVAL_S)
        raise SetupRefusal("server readiness timeout")

    def create_session(self) -> str:
        """Materialize the single session via the ``/sse`` handshake."""
        if self.server_url is None:
            raise SetupRefusal("start() must run before create_session()")
        session_id = str(uuid.uuid4())
        params = urllib.parse.urlencode(
            {"session_id": session_id, "file": str(self.notebook)}
        )
        with urllib.request.urlopen(
            f"{self.server_url}/sse?{params}", timeout=self.HANDLE_TIMEOUT_S
        ) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    break
            else:
                raise SetupRefusal("session handshake produced no kernel-ready event")
        self.session_id = session_id
        return session_id

    def census(self) -> dict:
        """Read the server's session census (``GET /api/sessions``)."""
        if self.server_url is None:
            raise SetupRefusal("start() must run before census()")
        with urllib.request.urlopen(
            self.server_url + "/api/sessions", timeout=self.CENSUS_TIMEOUT_S
        ) as response:
            return json.load(response)

    def stop(self) -> None:
        """Terminate and reap the server process."""
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None


def _default_factory(notebook: Path, python: str, state_dir: Path) -> ServerSession:
    return ServerSession(notebook, python, state_dir)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage one disposable comparison arm. The notebook a server opens "
            "is derived from --canonical; no target selector is accepted."
        )
    )
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--arm", required=True, choices=list(ARMS))
    parser.add_argument("--state-dir", required=True, dest="state_dir")
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--control-dir",
        dest="control_dir",
        default=None,
        help=(
            "write a second copy of the arm's identity (anchor.json: the"
            " identity fields plus the SHA-256 of the manifest file's bytes)"
            " into this directory. The caller must place it where the arm"
            " cannot write; the grader refuses unless this copy still pins the"
            " manifest. The arm-visible manifest is unchanged."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: stage an arm, writing a JSON error on refusal."""
    args = _parse_args(argv)
    try:
        manifest = perform_setup(
            args.canonical,
            args.root,
            args.python,
            args.arm,
            args.state_dir,
            args.manifest,
            server_factory=_default_factory,
            control_dir=args.control_dir,
        )
    except SetupRefusal as exc:
        print(
            json.dumps({"error": "setup_refused", "detail": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
