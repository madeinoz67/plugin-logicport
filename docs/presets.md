# Configuration Presets

This document describes the configuration presets of the LogicPort plugin. A preset is a complete settings document for the logic analyser configure action. The plugin keeps the presets under `src/logicport_plugin/ui/presets/`. The settings schema lives under `src/logicport_plugin/ui/settings/`.

A preset holds every setting of the configure action. The gateway inserts no default values. Selecting a preset performs no device input or output. Applying a preset is a separate operation. The apply operation requires the normal procedure and approval checks.

## Common settings

All presets share these settings:

| Setting | Value | Reason |
| --- | --- | --- |
| `sample_count` | `2048` | The device stores 2048 sample columns for 34 channels. See the user guide, section 6. |
| `trigger.kind` | `immediate` or `edge` | The configure action supports these trigger kinds. The plugin does not implement the software trigger action. So no preset uses the `software` kind. |

The device takes one threshold value for all channels. The threshold range is +6 V to -6 V in 50 mV steps. Each channel row in a preset repeats the shared threshold value. The sample rate range for timing mode is 1 kHz to 500 MHz. See the user guide, section 7.

## The presets

### Default timing capture

File: `presets/default-timing.json`. Identity: `preset-default-timing`.

| Setting | Value |
| --- | --- |
| Sample rate | 10 MHz |
| Threshold | 1.65 V |
| Channels | All 34 channels |
| Trigger | Immediate |

Use this preset for general logic debug on 3.3 V logic. The threshold of 1.65 V is one half of a 3.3 V supply. The rate of 10 MHz gives a capture window of about 205 microseconds. The run length encoding extends this window when the signals change slowly.

### Fast capture

File: `presets/fast-capture.json`. Identity: `preset-fast-capture`.

| Setting | Value |
| --- | --- |
| Sample rate | 100 MHz |
| Threshold | 1.65 V |
| Channels | D0 to D7, CLK1 and CLK2 |
| Trigger | Immediate |

Use this preset for fast signals on one 8 bit bus. The channel set matches bus A and the two clock lanes of the capture view. The rate of 100 MHz resolves signal edges of 10 nanoseconds or slower.

### TTL edge trigger on CLK1

File: `presets/ttl-edge-trigger.json`. Identity: `preset-ttl-edge-trigger`.

| Setting | Value |
| --- | --- |
| Sample rate | 10 MHz |
| Threshold | 1.4 V |
| Channels | D0 to D7 and CLK1 |
| Trigger | Edge, CLK1, rising, 1.4 V |

Use this preset to capture bus A around one clock edge of TTL logic. The trigger waits for a rising edge on CLK1. The capture buffer fills around that edge. The threshold of 1.4 V is the standard TTL input threshold.

### 5 V CMOS capture

File: `presets/cmos-5v.json`. Identity: `preset-cmos-5v`.

| Setting | Value |
| --- | --- |
| Sample rate | 10 MHz |
| Threshold | 2.5 V |
| Channels | All 34 channels |
| Trigger | Immediate |

Use this preset for 5 V CMOS logic. The threshold of 2.5 V is one half of a 5 V supply. Do not use this preset on 3.3 V logic. A threshold above the supply level does not cross at the correct input transition.

## Firmware statement

Each preset lists the firmware value `0x0400`. This value is the USB device release number of the LogicPort hardware. The protocol record confirms this number from a live device check.

The device descriptor declares the commissioned identity policy. Under this policy the bench commissioning record is the authority for device identity. The offline preset check admits a preset with no firmware value under this policy. When the check runs with a firmware value, the value must be one the preset lists.

The commissioning record of your bench must hold the value `0x0400`. A preset apply with a different established firmware value fails with a firmware mismatch. Change the `supported_firmware` row of each preset when your commissioning record holds a different value.

## Apply posture

The configure action refuses with `UNSUPPORTED` at apply time. The refusal is loud by design. The destination registers of the configuration writes are open items in the protocol record. The action validates the preset input first. A malformed input reports `INVALID_ARGUMENT`. A valid input reports the `UNSUPPORTED` refusal until the destinations are decoded. The standalone threshold write works today through the `write threshold_v` operation.

The presets are complete and valid documents now. They pass the offline checks. The apply path opens when the configuration destinations land in the protocol record.

## Offline checks

Run this command from the project root to check one preset offline:

```sh
benchweave-sdk check-preset src/logicport_plugin/ui/presets/default-timing.json \
  --descriptor src/logicport_plugin/descriptor.json \
  --settings-schema src/logicport_plugin/ui/settings/logic-analyser-configure.schema.json \
  --firmware 0x0400
```

A check pass is not admission. A check pass is not approval to apply settings. A check pass is not a hardware qualification.
