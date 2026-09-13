"""External API adapter domain implementations for cold-start convergence evaluation.

Domain C Challenge:
- Live-wire integration with google.antigravity or MCP tool call contracts.
- Defective implementation (SpeculativeToolResultAdapter):
  Relies on speculative getattr guessing (getattr(result, 'args', None)) and blanket exception
  suppression. As proven by fixtures/tool_result_fixture.json, ToolResult does NOT contain 'args'
  or 'duration_s'. Telemetry degrades silently to None without failing tests that don't inspect
  the telemetry record.
- Remediated implementation (RealWireToolResultAdapter):
  Pairs pre-call and post-call hooks. Captures call.args and start timestamp during pre_call,
  correlates with post_call via id or FIFO queue, computes duration_s, and fails loudly
  on schema violations.
"""
import collections
import time
from typing import Any


class SpeculativeToolResultAdapter:
    """Defective adapter relying on speculative getattr and blanket fallbacks."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def on_tool_call(self, call: Any) -> None:
        # Does not correlate pre-call state
        pass

    def on_tool_result(self, result: Any) -> dict[str, Any]:
        # DEFECT: Speculative getattr guessing on attributes that do not exist on ToolResult
        try:
            name = getattr(result, "name", "unknown")
            if hasattr(name, "value"):
                name = name.value
            tool_args = getattr(result, "args", None)  # ToolResult does NOT have args!
            res_val = getattr(result, "result", None)
            dur = getattr(result, "duration_s", None)  # ToolResult does NOT have duration_s!
            err = getattr(result, "error", None)

            record = {
                "event": "tool_call",
                "tool": str(name),
                "args": tool_args,
                "result": res_val,
                "duration_s": dur,
                "error": err,
            }
            self.events.append(record)
            return record
        except Exception:
            # DEFECT: Silent degradation / error swallowing
            return {}


class RealWireToolResultAdapter:
    """Remediated adapter enforcing Phase 0 wire contract and strict correlation."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self._pending_by_id: dict[str, tuple[Any, float]] = {}
        self._pending_fifo: collections.deque[tuple[Any, float]] = collections.deque()

    def on_tool_call(self, call: Any) -> None:
        """Phase 0 pre-call hook capturing call.args and timestamp directly."""
        t0 = time.monotonic()
        call_id = getattr(call, "id", None)
        if call_id:
            self._pending_by_id[call_id] = (call, t0)
        else:
            self._pending_fifo.append((call, t0))

    def on_tool_result(self, result: Any) -> dict[str, Any]:
        """Correlates with pre-call hook, computes elapsed time, fails loudly if missing."""
        t1 = time.monotonic()
        res_id = getattr(result, "id", None)

        if res_id and res_id in self._pending_by_id:
            call, t0 = self._pending_by_id.pop(res_id)
        elif self._pending_fifo:
            call, t0 = self._pending_fifo.popleft()
        else:
            raise KeyError(f"No matching pre_tool_call found for ToolResult id={res_id}")

        # Extract name directly
        raw_name = result.name
        tool_name = raw_name.value if hasattr(raw_name, "value") else str(raw_name)

        # Extract args directly from the correlated ToolCall
        tool_args = call.args
        if not isinstance(tool_args, dict):
            raise TypeError(f"ToolCall.args must be dict, got {type(tool_args)}")

        # Extract result & error
        res_val = result.result
        err_val = result.error
        if err_val is None and getattr(result, "exception", None) is not None:
            err_val = str(result.exception)

        duration = max(0.0, t1 - t0)

        record = {
            "event": "tool_call",
            "tool": tool_name,
            "args": tool_args,
            "result": res_val,
            "duration_s": duration,
            "error": err_val,
        }
        self.events.append(record)
        return record
