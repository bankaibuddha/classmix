"""Model broker: the only thing the sandboxed agent can reach.

Runs on the HOST, outside the container. Listens on a unix domain socket
that is bind-mounted into an agent container started with --network none.
The container therefore has no IP stack at all: no DNS, no TCP, no route to
the LAN, and no way to reach any endpoint but this one socket.

Design rule: the agent supplies TEXT and nothing else. It does not choose
the model, the URL, the sampling options, or the request shape. This broker
constructs the entire upstream call from its own configuration. A prompt
that asks the broker to talk to a different host is just text in a prompt
field, because there is no field that could carry a host.

This is also the second, independent budget enforcer. The in-container loop
counts its own iterations, but that loop is code the agent could in
principle corrupt. The broker cannot be reached by anything except through
this socket, runs in a different process outside the sandbox, and stops
answering at its cap regardless of what the agent believes. A control
exists only if it fires, so the caps are exercised by the self-tests below.

Self-tests (no network, no Ollama, no docker required):

    python3 broker/broker.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Hard ceilings. These bound one request; Budget bounds the whole run.
MAX_REQUEST_BYTES = 256 * 1024
MAX_SYSTEM_CHARS = 32_000
MAX_PROMPT_CHARS = 200_000
ALLOWED_KEYS = frozenset({"system", "prompt"})


class BudgetExhausted(Exception):
    """The run hit a cap. Raised by Budget, reported to the agent as a refusal."""


class RequestRefused(Exception):
    """The request was malformed or carried a field the broker does not accept."""


@dataclass
class Budget:
    """Bounds a whole run. Every field is a hard stop, not a warning."""

    max_requests: int = 200
    max_total_chars: int = 8_000_000
    max_seconds: float = 3 * 60 * 60

    requests: int = 0
    total_chars: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def spend(self, chars: int) -> None:
        elapsed = time.monotonic() - self.started_at
        if elapsed >= self.max_seconds:
            raise BudgetExhausted(
                f"wall clock exhausted: {elapsed:.0f}s of {self.max_seconds:.0f}s"
            )
        if self.requests >= self.max_requests:
            raise BudgetExhausted(
                f"request cap exhausted: {self.requests} of {self.max_requests}"
            )
        if self.total_chars + chars > self.max_total_chars:
            raise BudgetExhausted(
                f"character cap exhausted: {self.total_chars + chars} would exceed "
                f"{self.max_total_chars}"
            )
        self.requests += 1
        self.total_chars += chars

    def snapshot(self) -> dict:
        return {
            "requests": self.requests,
            "max_requests": self.max_requests,
            "total_chars": self.total_chars,
            "max_total_chars": self.max_total_chars,
            "elapsed_s": round(time.monotonic() - self.started_at, 1),
            "max_seconds": self.max_seconds,
        }


def ollama_upstream(base_url: str, model: str, timeout: float):
    """Build the one upstream call this broker is allowed to make.

    The URL and model are closed over here, at construction time, from the
    operator's arguments. Nothing from the agent reaches this function
    except the two text strings.
    """

    endpoint = base_url.rstrip("/") + "/api/chat"

    def call(system: str, prompt: str) -> str:
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")

    return call


class Broker:
    """Validates, budgets, forwards, and journals. Nothing else."""

    def __init__(self, upstream, budget: Budget, journal_path: Path | None = None):
        self.upstream = upstream
        self.budget = budget
        self.journal_path = Path(journal_path) if journal_path else None
        self._lock = threading.Lock()
        self._journal_lock = threading.Lock()

    def handle_raw(self, raw: bytes) -> dict:
        """Take one request line, return one response dict. Never raises."""
        try:
            request = self._parse(raw)
        except RequestRefused as exc:
            self._journal({"event": "refused", "reason": str(exc)})
            return {"ok": False, "error": f"refused: {exc}", "kind": "refused"}

        chars = len(request["system"]) + len(request["prompt"])
        try:
            with self._lock:
                self.budget.spend(chars)
        except BudgetExhausted as exc:
            self._journal({"event": "budget_exhausted", "reason": str(exc)})
            return {
                "ok": False,
                "error": f"budget exhausted: {exc}",
                "kind": "budget",
                "budget": self.budget.snapshot(),
            }

        try:
            text = self.upstream(request["system"], request["prompt"])
        except Exception as exc:  # upstream is a network call; never crash the broker
            self._journal({"event": "upstream_error", "reason": repr(exc)})
            return {"ok": False, "error": f"upstream error: {exc}", "kind": "upstream"}

        self._journal(
            {
                "event": "ok",
                "prompt_chars": len(request["prompt"]),
                "reply_chars": len(text),
                "budget": self.budget.snapshot(),
            }
        )
        return {"ok": True, "text": text, "budget": self.budget.snapshot()}

    def _parse(self, raw: bytes) -> dict:
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestRefused(f"request of {len(raw)} bytes exceeds {MAX_REQUEST_BYTES}")
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestRefused(f"not valid utf-8 json: {exc}") from exc
        if not isinstance(request, dict):
            raise RequestRefused("top level must be an object")

        # Allowlist, not denylist. An unknown key is a refusal, not something
        # to ignore, so a future field cannot be smuggled past an old broker.
        unknown = set(request) - ALLOWED_KEYS
        if unknown:
            raise RequestRefused(f"unknown field(s): {sorted(unknown)}")
        for key in ("system", "prompt"):
            if key not in request:
                raise RequestRefused(f"missing field: {key}")
            if not isinstance(request[key], str):
                raise RequestRefused(f"field {key} must be a string")
        if len(request["system"]) > MAX_SYSTEM_CHARS:
            raise RequestRefused(f"system exceeds {MAX_SYSTEM_CHARS} chars")
        if len(request["prompt"]) > MAX_PROMPT_CHARS:
            raise RequestRefused(f"prompt exceeds {MAX_PROMPT_CHARS} chars")
        return request

    def _journal(self, entry: dict) -> None:
        if not self.journal_path:
            return
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with self._journal_lock:
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return
        response = self.server.broker.handle_raw(raw.strip())
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path, broker):
        super().__init__(path, _Handler)
        self.broker = broker


def serve(socket_path: Path, broker: Broker) -> _Server:
    """Bind the socket 0600 and start serving. Caller owns the shutdown."""
    socket_path = Path(socket_path)
    if socket_path.exists():
        socket_path.unlink()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    server = _Server(str(socket_path), broker)
    # 0600: only the invoking user. The container runs as that same uid, so
    # it can speak; nothing else on the box can.
    os.chmod(socket_path, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def ask(socket_path: Path, system: str, prompt: str, timeout: float = 600.0) -> dict:
    """Client side. Used by the in-container agent and by the self-tests."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall((json.dumps({"system": system, "prompt": prompt}) + "\n").encode("utf-8"))
        chunks = []
        while not chunks or not chunks[-1].endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


# -- self-tests ---------------------------------------------------------


class BrokerTests(unittest.TestCase):
    def make(self, **budget_kwargs):
        calls = []

        def upstream(system, prompt):
            calls.append((system, prompt))
            return f"reply {len(calls)}"

        broker = Broker(upstream, Budget(**budget_kwargs))
        return broker, calls

    def test_forwards_a_well_formed_request(self):
        broker, calls = self.make()
        result = broker.handle_raw(b'{"system":"s","prompt":"p"}')
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "reply 1")
        self.assertEqual(calls, [("s", "p")])

    def test_request_cap_fires_and_stops_forwarding(self):
        broker, calls = self.make(max_requests=2)
        for _ in range(2):
            self.assertTrue(broker.handle_raw(b'{"system":"s","prompt":"p"}')["ok"])
        blocked = broker.handle_raw(b'{"system":"s","prompt":"p"}')
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["kind"], "budget")
        # The point of the cap: upstream was not called a third time.
        self.assertEqual(len(calls), 2)

    def test_character_cap_fires(self):
        broker, calls = self.make(max_total_chars=10)
        blocked = broker.handle_raw(json.dumps({"system": "", "prompt": "x" * 50}).encode())
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["kind"], "budget")
        self.assertEqual(calls, [])

    def test_wall_clock_cap_fires(self):
        broker, calls = self.make(max_seconds=0.0)
        blocked = broker.handle_raw(b'{"system":"s","prompt":"p"}')
        self.assertFalse(blocked["ok"])
        self.assertIn("wall clock", blocked["error"])
        self.assertEqual(calls, [])

    def test_refuses_unknown_field(self):
        # The interesting case: an agent trying to redirect the upstream.
        broker, calls = self.make()
        result = broker.handle_raw(
            b'{"system":"s","prompt":"p","url":"http://evil.example/x"}'
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "refused")
        self.assertIn("url", result["error"])
        self.assertEqual(calls, [])

    def test_refuses_model_override(self):
        broker, calls = self.make()
        result = broker.handle_raw(b'{"system":"s","prompt":"p","model":"other"}')
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [])

    def test_refuses_wrong_types_and_missing_fields(self):
        broker, _ = self.make()
        for raw in (b'{"system":1,"prompt":"p"}', b'{"prompt":"p"}', b'[]', b'not json'):
            result = broker.handle_raw(raw)
            self.assertFalse(result["ok"], raw)
            self.assertEqual(result["kind"], "refused", raw)

    def test_refuses_oversize_request(self):
        broker, calls = self.make()
        result = broker.handle_raw(b"x" * (MAX_REQUEST_BYTES + 1))
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [])

    def test_upstream_failure_does_not_crash_the_broker(self):
        def upstream(system, prompt):
            raise urllib.error.URLError("connection refused")

        broker = Broker(upstream, Budget())
        result = broker.handle_raw(b'{"system":"s","prompt":"p"}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "upstream")
        # Still serving afterwards.
        self.assertFalse(broker.handle_raw(b'{"bad":1}')["ok"])

    def test_end_to_end_over_a_real_unix_socket(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.sock"
            broker, _ = self.make(max_requests=1)
            server = serve(path, broker)
            try:
                self.assertEqual(oct(path.stat().st_mode)[-3:], "600")
                self.assertEqual(ask(path, "s", "p")["text"], "reply 1")
                second = ask(path, "s", "p")
                self.assertFalse(second["ok"])
                self.assertEqual(second["kind"], "budget")
            finally:
                server.shutdown()
                server.server_close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--socket", type=Path, help="unix socket path to bind")
    parser.add_argument("--model", default="qwen2.5-coder:32b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--max-total-chars", type=int, default=8_000_000)
    parser.add_argument("--max-seconds", type=float, default=3 * 60 * 60)
    parser.add_argument("--upstream-timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrokerTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    if not args.socket:
        parser.error("--socket is required unless --self-test")

    if not args.base_url.startswith("http://127.0.0.1") and not args.base_url.startswith(
        "http://localhost"
    ):
        # v1 is single-box by design. A non-loopback base URL means the model
        # endpoint is on the LAN, and Ollama has no authentication.
        print(
            f"REFUSING --base-url {args.base_url}: v1 is loopback-only.\n"
            "A non-loopback model endpoint is an unauthenticated service on your\n"
            "network. If you want cross-box inference, forward it over SSH and\n"
            "point this at the local end of the tunnel.",
            file=sys.stderr,
        )
        return 2

    broker = Broker(
        upstream=ollama_upstream(args.base_url, args.model, args.upstream_timeout),
        budget=Budget(
            max_requests=args.max_requests,
            max_total_chars=args.max_total_chars,
            max_seconds=args.max_seconds,
        ),
        journal_path=args.journal,
    )
    server = serve(args.socket, broker)
    print(f"broker listening on {args.socket} -> {args.base_url} ({args.model})", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        if Path(args.socket).exists():
            Path(args.socket).unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
