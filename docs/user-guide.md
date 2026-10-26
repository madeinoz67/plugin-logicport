# LogicPort Plugin — User Guide

This guide explains how to install, set up, and use the LogicPort plugin. The plugin drives the Intronix LogicPort logic analyzer from BenchWeave.

## 1. What This Plugin Does

The plugin talks to the LogicPort hardware over USB. It supports these operations:

- Open the device and set up the connection.
- Load the FPGA design from the vendor file.
- Set the sample rate.
- Set the input threshold.
- Set the trigger terms and the trigger sequencer.
- Start a capture and read the status.
- Read the sample data and decode the 34 channels.
- Stop a capture.

## 2. What You Need

- An Intronix LogicPort logic analyzer, connected by USB.
- The `libftdi` library. On macOS, run `brew install libftdi`. On Linux, install it from your package manager.
- Python 3.13 or later.
- The vendor file `LogicPort.ccf`. See section 3.

WARNING: THE DEVICE USES USB POWER AND CONNECTS TO VOLTAGES UP TO 40 V DC. CHECK THE PROBE CONNECTIONS BEFORE YOU ATTACH THEM TO A CIRCUIT. WRONG CONNECTIONS CAN DAMAGE THE DEVICE AND THE CIRCUIT UNDER TEST.

## 3. How to Get the Vendor Files

This plugin does not contain vendor files. You must get them from the vendor.

1. Go to the Intronix Test Instruments website.
2. Download the LogicPort support software.
3. Install the software on any Windows computer.
4. Find the file `LogicPort.ccf` in the installation directory.
5. Copy that file to a folder of your choice.

The file must be 8,387,769 bytes. Its SHA-256 checksum is:

```
9298a5eacb3cf1a791b8ce48460a19e031eb81baffa378bab3f94f2d78483f9c
```

The plugin checks this checksum when you start a capture. A mismatch means the file is wrong or damaged. Get a new copy from the vendor.

You do not need the LogicPort application at run time. You need only the `LogicPort.ccf` file.

## 4. Install the Plugin

1. Install Python 3.13 or later.
2. Install `libftdi` (section 2).
3. Download this plugin package.
4. Create a virtual environment in the plugin directory.
5. Run `pip install .` in the plugin directory.

## 5. Configure the Plugin

The plugin has one required setting:

| Setting | Meaning |
|---|---|
| `ccf_path` | The full path to your `LogicPort.ccf` file |

Set `ccf_path` in the plugin configuration. The plugin reads the file when you load the FPGA design.

CAUTION: THE PLUGIN WRITES TO THE DEVICE WHEN IT LOADS THE FPGA DESIGN. DO NOT UNPLUG THE DEVICE DURING A LOAD. A BROKEN LOAD NEEDS A NEW LOAD AFTER YOU RECONNECT THE DEVICE.

## 6. How a Capture Works

Follow these steps to capture signals:

1. Connect the probes to the circuit under test.
2. Power the device. The USB connection supplies its power.
3. Configure the sample rate and the threshold.
4. Set the trigger terms. The device has four trigger levels.
5. Start the capture. The plugin loads the FPGA design, arms the device, and waits for the trigger.
6. Read the status. The status value is the command counter. The capture-complete decode ("status bit 0 is 1") is retracted, dated 2026-10-26, issue #562 — the recorded bits were the counter, and no completion gate is proven; the plugin drains the sample data when you fetch it.
7. Read the sample data. The plugin decodes the 34 channels for you.

The device stores 2048 sample columns for 34 channels. The sample data uses run-length encoding. The plugin expands the runs to the real sample times.

## 7. Channels and Modes

The device has 34 input channels: D0 to D31, plus two clock inputs CLK1 and CLK2.

- Timing mode uses the internal clock. The rate range is 1 kHz to 500 MHz.
- State mode uses an external clock. The rate range is 0 to 200 MHz.

The threshold is the same for all channels. The range is +6 V to -6 V.

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| The plugin cannot find the device | Check the USB cable. Check that `libftdi` is installed. |
| The capture never completes | Check the trigger terms. The device waits for a match. |
| The status check fails after the FPGA load | Unplug the device. Reconnect it. Run the load again. |
| The checksum check fails | Get a new copy of `LogicPort.ccf` from the vendor. |

## 9. For More Information

The wire protocol, the register map, and the data formats are in `docs/protocol-spec.md`. That document is the source of truth for the device protocol.
