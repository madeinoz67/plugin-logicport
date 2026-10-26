"""Descriptor/adapter machine-truth agreement (issue #562).

A declared surface the adapter refuses is a lie the host schedules around:
``side_effect`` drives concurrency and an advertised action is expected to
run. These tests pin the agreement both ways — every adapter-bound action
declared in the descriptor is served by the adapter, and every channel the
adapter serves is declared with the parameters that serve it.
"""

import json
from importlib.resources import files

from logicport_plugin.adapter import (
    _FREQUENCY_SLOTS,
    _INVOKE_ACTIONS,
    _READ_PARAMETERS,
)


def _descriptor():
    return json.loads(files("logicport_plugin").joinpath("descriptor.json").read_text())


def test_every_declared_action_is_served():
    descriptor = _descriptor()
    declared = {
        action_id
        for action_id, row in descriptor.get("actions", {}).items()
        if row.get("binding", {}).get("kind") == "adapter"
    }
    unserved = declared - set(_INVOKE_ACTIONS)
    assert not unserved, f"declared but refused by the adapter: {sorted(unserved)}"


def test_every_served_action_is_declared():
    descriptor = _descriptor()
    declared = set(descriptor.get("actions", {}))
    undeclared = set(_INVOKE_ACTIONS) - declared
    assert not undeclared, f"served by the adapter but absent from the descriptor: {sorted(undeclared)}"


def test_read_parameters_are_declared():
    descriptor = _descriptor()
    declared = {row["name"] for row in descriptor.get("parameters", [])}
    missing = set(_READ_PARAMETERS) - declared
    assert not missing, f"served read parameter missing from the descriptor: {sorted(missing)}"


def test_measure_channels_match_served_counter_slots():
    """The adapter serves four counter slots; the descriptor must say so.

    The vectors rehearse four channels and ``_FREQUENCY_SLOTS`` maps four
    parameters; a channel row that names a ``measure`` role and a frequency
    parameter is the descriptor's side of that agreement.
    """
    descriptor = _descriptor()
    frequency_channels = {
        row["id"]
        for row in descriptor.get("channels", [])
        if row.get("role") == "measure" and row.get("quantities") == ["frequency"]
    }
    served = set(_FREQUENCY_SLOTS)
    assert frequency_channels == served, (
        f"descriptor declares {sorted(frequency_channels)}; the adapter serves {sorted(served)}"
    )


def test_measure_channel_parameters_exist():
    descriptor = _descriptor()
    parameters = {row["name"] for row in descriptor.get("parameters", [])}
    for row in descriptor.get("channels", []):
        for name in row.get("parameter_names", []):
            assert name in parameters, f"channel {row['id']} names undeclared parameter {name!r}"
