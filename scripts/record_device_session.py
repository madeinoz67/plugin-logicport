"""Record a real LogicPort session and emit it as mock vector exchanges.

The rule this script exists to honour: a fixture that encodes what the
device did must come from the device. Hand-authoring the rows (even to
"match" a recorded counter sequence) writes down a belief and then agrees
with itself.

What it does: wraps the real pyftdi driver, drives the plugin exactly as
the host does, pairs each ``write_data`` with the bytes the following
``read_data`` returned, and writes the pairs as a JSON exchange list in
the ``src/logicport_plugin/vectors.json`` shape. Byte-level, so it
captures whatever the device really puts on the wire, including unpaired
writes and replies that arrive in an unexpected length.

Usage:  uv run python scripts/record_device_session.py OUT.json [--read-only]

``--read-only`` skips the ``configure_fpga`` chain and journals only the
read-class steps (status, trigger_position, read_counter, read_samples).
Use it to capture the exchanges of a LIVE device state — once the engine
is running, a configure would reset it; a read-only journal preserves the
state while recording what it puts on the wire.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

from pyftdi.ftdi import Ftdi
from benchweave_sdk_server.ftdi import _PyftdiDriver, ftdi_plugin_session
from benchweave_sdk_server.session import load_plugin_project

VENDOR, PRODUCT, INTERFACE = 0x0403, 0xDC48, 1


class Recording:
    """Driver wrapper that journals (write, read) pairs at the wire."""

    def __init__(self, inner):
        self._inner = inner
        self.exchanges: list[dict] = []
        self._pending: bytes | None = None

    # Every driver method except the byte movers passes straight through.
    def __getattr__(self, name):
        return getattr(self._inner, name)

    def write_data(self, data):
        result = self._inner.write_data(data)
        self._pending = bytes(data)
        return result

    def read_data(self, size):
        got = self._inner.read_data(size)
        if got is None:
            got = b""
        self.exchanges.append(
            {
                "request": self._pending.hex() if self._pending is not None else "",
                "response": bytes(got).hex(),
            }
        )
        self._pending = None
        return got


def main(out_path: str) -> int:
    rec: Recording | None = None

    def driver_factory(_url):
        nonlocal rec
        ftdi = Ftdi()
        ftdi.open(VENDOR, PRODUCT, interface=INTERFACE, index=0)
        rec = Recording(_PyftdiDriver(ftdi))
        return rec

    async def run():
        plugin = load_plugin_project(pathlib.Path(".").resolve())
        session = ftdi_plugin_session(
            plugin,
            "record",
            driver_factory=driver_factory,
            record_path=pathlib.Path("/tmp/logicport-journal.json"),
        )
        # Deliberate: the injected factory marks the session rehearsal, but the
        # driver behind it is the real device. This is a bench session.
        session.rehearsal = False
        await session.connect()
        # Instrument the TRANSFER layer, not the driver: the driver shows
        # bytes and says nothing about which op produced them.
        svc = session.services
        inner = svc.transfer
        xfers: list[dict] = []

        async def logged(transaction, context):
            outcome = await inner(transaction, context)
            xfers.append({"op": transaction.get("op"), "args": {
                k: (v.hex() if isinstance(v, bytes) else v)
                for k, v in transaction.items() if k != "op"
            }, "result": {k: (v.hex() if isinstance(v, bytes) else v)
                          for k, v in outcome.items()} if isinstance(outcome, dict) else outcome})
            return outcome

        svc.transfer = logged
        read_only = "--read-only" in sys.argv
        steps = (
            [
                (
                    "invoke",
                    {
                        "action_id": "configure_fpga",
                        "input": {
                            "attempts": 1,
                            "ccf_path": "/Users/seaton/Documents/src/logiport/extracted/files/LogicPort.ccf",
                        },
                    },
                ),
            ]
            if not read_only
            else []
        ) + [
            ("read", {"parameter": "status"}),
            ("read", {"parameter": "trigger_position"}),
            ("invoke", {"action_id": "read_counter", "input": {}}),
            (
                "invoke",
                {
                    "action_id": "read_samples",
                    "input": {"column_count": 64, "prescale": 0, "trigger_word": 0},
                },
            ),
        ]
        results = []
        for op, payload in steps:
            try:
                results.append({"op": op, "payload": payload, "result": await session.execute(op, payload)})
            except Exception as exc:  # the failure IS the evidence
                results.append({"op": op, "payload": payload, "error": f"{type(exc).__name__}: {exc}"})
        try:
            await session.close()
        except Exception as exc:
            results.append({"op": "close", "payload": {}, "error": f"{type(exc).__name__}: {exc}"})
        return results, xfers

    results, xfers = asyncio.run(run())

    payload = {
        "exchanges": (rec.exchanges if rec else []),
        "transfers": xfers,
        "session": results,
    }
    pathlib.Path(out_path).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(payload['exchanges'])} exchanges to {out_path}")
    for row in results:
        status = row.get("error") or (row["result"] or {}).get("status", "?")
        print(f"  {row['op']:7} {row['payload'].get('action_id') or row['payload'].get('parameter'):24} {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/logicport-session.json"))
