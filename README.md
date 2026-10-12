# Device plugin starter

Synthetic only. Create a virtual environment, install the matching SDK wheel and `uv pip install -e '.[test]'`. Run `pytest`, then `uv build`. Pin dependencies with `uv lock`. See AI-GUIDE.md. Choose a licence before distribution.

## Obtaining vendor files (not distributed with this plugin)

This plugin does not ship Intronix or FTDI proprietary content. Three vendor items exist; all are obtained from their vendors:

1. **`LogicPort.ccf`** — the FPGA bitstream (8,387,769 bytes, SHA-256 `9298a5eacb3cf1a791b8ce48460a19e031eb81baffa378bab3f94f2d78483f9c`). Install the official LogicPort support software from Intronix Test Instruments. The file sits in the installation directory. Configure the plugin's `ccf_path` option to point at it. The plugin verifies the SHA-256 when the file is present.

2. **FTDI drivers** — on macOS and Linux the plugin uses the open-source `libftdi` (install via your package manager; Homebrew: `brew install libftdi`). On Windows, install the drivers from the official LogicPort software. The `ftd2xx.dll` and `ftdibus.sys` files in the vendor installer are not needed here and must not be redistributed.

3. **The LogicPort application** (`LogicPort.exe`) — not needed at runtime. It is referenced only as the reverse-engineering source in the protocol documentation.

The hardware protocol itself is documented in `docs/protocol-spec.md` for interoperability. Functional facts (command formats, register maps) are documented; no vendor file bytes are reproduced.
