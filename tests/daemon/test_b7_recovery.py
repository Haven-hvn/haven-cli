"""Regression tests for B7 (daemon auto-recovery / audible failure).

Covers:
  * Deno-only JS runtime discovery (no tsx/ts-node/Node/Bun fallback that
    produced ERR_UNSUPPORTED_ESM_URL_SCHEME).
  * Immediate bridge-death detection + restart callback wiring.
  * Unbuffered logging helper is importable and idempotent.
"""

import asyncio
import logging
from pathlib import Path

import pytest

from haven_cli.js_runtime import discovery
from haven_cli.js_runtime.bridge import JSRuntimeBridge, RuntimeState
from haven_cli.js_runtime.manager import JSBridgeManager


class TestDenoOnlyRuntime:
    """The JS services are Deno-only; other runtimes must be rejected."""

    def test_deno_args(self):
        args = discovery.get_runtime_args(
            "/usr/local/bin/deno", Path("js-services/main.ts"), debug=False
        )
        assert args[0] == "/usr/local/bin/deno"
        assert args[1] == "run"
        assert "--allow-net" in args
        assert str(args[-1]).endswith("main.ts")

    def test_deno_debug_inspect(self):
        args = discovery.get_runtime_args(
            "/usr/local/bin/deno", Path("js-services/main.ts"), debug=True
        )
        assert "--inspect" in args

    @pytest.mark.parametrize("exe", ["/usr/bin/node", "/usr/bin/nodejs", "/usr/bin/bun"])
    def test_non_deno_rejected(self, exe):
        with pytest.raises(RuntimeError) as exc:
            discovery.get_runtime_args(exe, Path("js-services/main.ts"), False)
        assert "Deno" in str(exc.value)

    def test_preference_is_deno_only(self):
        assert discovery.RUNTIME_PREFERENCE == [discovery.RuntimeType.DENO]

    def test_discover_runtime_sync_returns_deno(self):
        # The CI/dev environment ships Deno; discovery must resolve it.
        exe = discovery.discover_runtime_sync()
        assert "deno" in Path(exe).name.lower()


class TestBridgeDeathDetection:
    """A killed bridge must be marked dead and trigger the restart callback."""

    @pytest.mark.asyncio
    async def test_unexpected_exit_marks_error_and_calls_callback(self):
        bridge = JSRuntimeBridge()
        fired = asyncio.Event()

        async def on_death():
            fired.set()

        bridge.set_on_unexpected_exit(on_death)
        # Simulate a live bridge whose stdout reaches EOF.
        bridge._state = RuntimeState.READY
        bridge._ready_event.set()

        # A fake stdout whose readline() returns b"" (EOF).
        class _Stdout:
            async def readline(self):
                return b""

        class _Proc:
            stdout = _Stdout()

        bridge._process = _Proc()
        await bridge._read_loop()

        assert bridge.state == RuntimeState.ERROR
        assert not bridge.is_ready
        assert not bridge._ready_event.is_set()
        # Callback is scheduled via create_task; let the loop run once.
        await asyncio.sleep(0)
        assert fired.is_set()

    @pytest.mark.asyncio
    async def test_graceful_stop_does_not_trigger_callback(self):
        bridge = JSRuntimeBridge()
        fired = asyncio.Event()

        async def on_death():
            fired.set()

        bridge.set_on_unexpected_exit(on_death)
        # SHUTTING_DOWN / STOPPED states must not be treated as unexpected.
        bridge._state = RuntimeState.SHUTTING_DOWN

        class _Stdout:
            async def readline(self):
                return b""

        class _Proc:
            stdout = _Stdout()

        bridge._process = _Proc()
        await bridge._read_loop()
        await asyncio.sleep(0)
        assert not fired.is_set()

    @pytest.mark.asyncio
    async def test_pending_futures_failed_on_death(self):
        bridge = JSRuntimeBridge()
        bridge._state = RuntimeState.READY
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        bridge._pending_futures["req-1"] = fut

        class _Stdout:
            async def readline(self):
                return b""

        class _Proc:
            stdout = _Stdout()

        bridge._process = _Proc()
        await bridge._read_loop()
        assert fut.done()
        with pytest.raises(RuntimeError):
            fut.result()


class TestManagerWiresDeathCallback:
    """_create_bridge must register the on-death restart callback (B7)."""

    @pytest.mark.asyncio
    async def test_create_bridge_registers_callback(self):
        JSBridgeManager.reset_instance()
        mgr = JSBridgeManager.get_instance()
        # Avoid spawning a real Deno process: patch JSRuntimeBridge.start.
        with pytest.MonkeyPatch.context() as mp:
            async def fake_start(self):
                self._state = RuntimeState.READY
            mp.setattr(JSRuntimeBridge, "start", fake_start)
            bridge = await mgr._create_bridge()
        assert bridge._on_unexpected_exit is not None
        JSBridgeManager.reset_instance()


class TestUnbufferedLogging:
    """The B7 logging helper is idempotent and does not change log level."""

    def test_force_unbuffered_logging_idempotent(self):
        from haven_cli.daemon.service import _force_unbuffered_logging

        root = logging.getLogger()
        before_level = root.level
        _force_unbuffered_logging()
        # Calling twice must not stack duplicate flush handlers.
        _force_unbuffered_logging()
        flush_handlers = [
            h for h in root.handlers if type(h).__name__ == "_FlushHandler"
        ]
        assert len(flush_handlers) == 1
        assert root.level == before_level
