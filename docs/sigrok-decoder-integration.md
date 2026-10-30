# pdhost: Sigrok-Compatible Decoder Hosting Library. Analysis & Design Note

## Home Decision (owner, 2026-10-12)

The module lives in the SDK as `benchweave_sdk.decode`. No separate repository. The dependency count for consumers stays flat (the plugin already depends on the SDK). Extract to a standalone repository only when a consumer outside the SDK's dependency tree appears; the package boundary lifts out unchanged. The earlier working name `pdhost` is retired; the design below is otherwise unchanged.


Local analysis doc, not committed. Source: shallow clone of `sigrokproject/libsigrokdecode` at
`sigrokdecode-src` (GPLv3+; PD files individually marked
`license = 'gplv2+'` in their Decoder class attributes). Analysis date: 2026-10-12.

**Direction (owner, 2026-10-12):** the decoder layer is a standalone common Python library,
working name **`pdhost`** (protocol decoder host). The LogicPort plugin is its first consumer
but nothing LogicPort-specific enters the library: producers supply named channels + sample
data; pdhost loads and drives sigrok-compatible protocol decoders and yields annotation
packets. The plugin repo is untouched by this note.

---

## 0. Executive summary

1. A sigrok PD is a Python package exposing `class Decoder` with `api_version == 3`. The
   complete runtime surface a PD uses is **7 module names** (`Decoder`, `OUTPUT_ANN`,
   `OUTPUT_PYTHON`, `OUTPUT_BINARY`, `OUTPUT_LOGIC`, `OUTPUT_META`, `SRD_CONF_SAMPLERATE`)
   and **6 instance members** (`wait`, `put`, `register`, `has_channel`, `samplenum`,
   `matched`), verified by import/usage tally across all 132 PDs.
2. The `sigrokdecode` module PDs import is an 88-line C stub with zero protocol logic; the
   PD corpus is self-contained pure Python (stdlib + own `common/` helpers only). Therefore a
   pure-Python shim (≈200 lines, written from the documented interface) hosts the entire
   upstream PD corpus without any GPL C library.
3. **Library license recommendation: MIT core, in-process loading of user-supplied PDs, PD
   corpus never bundled by the library; an optional out-of-process runner mode provides hard
   isolation.** Rationale and caveats in §5.

## 1. PD (Protocol Decoder) API contract

### 1.1 Module layout (one PD = one Python package)

```
decoders/
  i2c/
    __init__.py   # docstring + "from .pd import Decoder"   (nothing else)
    pd.py         # class Decoder(srd.Decoder), the whole decoder
  common/
    srdhelper.py  # pure-Python helpers: bitpack, bitpack_msb/lsb, bcd2int, SrdIntEnum...
    sdcard.py     # used only by sdcard-related PDs
```

There is **no metadata file** (the old `.pd` format is gone). Discovery = import the package,
read class attributes. `srd_decoder_load(name)` imports by name with the searchpaths on
`sys.path`, needs `Decoder` to be a subclass of `sigrokdecode.Decoder` and
`api_version == 3` (the only supported version; anything else is rejected at load).

### 1.2 Class-surface declarations (class attributes, plain data)

| Attribute | Shape | Notes |
|---|---|---|
| `api_version` | `3` | load-gate |
| `id` / `name` / `longname` / `desc` / `license` / `tags` | strings / tuple | `id` is the registry key (`'i2c'`); `tags` optional tuple |
| `inputs` | tuple of strings, e.g. `('logic',)` | `'logic'` = raw samples; matching another PD's `outputs` string enables stacking |
| `outputs` | tuple, e.g. `('i2c',)` | what it emits for stacking |
| `channels` | tuple of dicts `{'id','name','desc'}` | **needed** channels; tuple index = the number PD code uses in `wait({0: 'r'})` |
| `optional_channels` | same shape | mapped or left unconnected; unconnected channels read as `0xff` in pinvalues |
| `options` | tuple of `{'id','desc','default','values'}` | host materializes `self.options` dict (defaults merged) before `start()` |
| `annotations` | tuple of `(id, label)` pairs | **index** = annotation class number passed to `put()` |
| `annotation_rows` | tuple of `(row_id, label, (ann_class_idxs...))` | UI grouping only |
| `binary` | tuple of `(id, label)` | binary-output classes, index-based |

### 1.3 Lifecycle hooks (all optional except `decode`)

- `__init__` → `reset()`, fresh state.
- `metadata(key, value)`, only `srd.SRD_CONF_SAMPLERATE` (int 10000) is ever delivered; PDs
  store `self.samplerate` for time math. Delivered to every instance in the stack.
- `start()`. PD calls `self.register(srd.OUTPUT_ANN / OUTPUT_PYTHON / OUTPUT_BINARY /
  OUTPUT_META(meta=(type,name,descr)))` and stores returned small-int output ids.
- `decode()`, the main loop. **Bottom PD**: `def decode(self)`, no args, an infinite
  `while True:` loop around `self.wait(...)`; runs once until it raises. `EOFError` at end of
  stream is a sanctioned termination signal.
- `flush()`, optional, called after each sample chunk is consumed (end-of-capture tail).
- Stacked (top) PDs instead get **`decode(self, ss, es, data)`**, a plain function invoked
  synchronously per OUTPUT_PYTHON packet emitted by the decoder below them. They may not
  use `wait()`.

### 1.4 The instance API the PD code actually uses (complete)

| Member | Semantics |
|---|---|
| `self.wait(cond)` | Blocks until the next matching sample. `cond` is a dict `{channel_idx: term}` (all terms must hold) or a **list** of such dicts (any may match). Terms: `'h'` high, `'l'` low, `'r'` rising, `'f'` falling, `'e'` either edge, and `'skip': N` as a **string key** (`{'skip': 5}`) to advance N samples. Empty/None cond = next sample. Returns the tuple of **pin values at the matched sample** (one per declared channel, in declaration order; unconnected optional channels are `0xff`). Raises `EOFError` when the sample stream is exhausted. |
| `self.samplenum` | Absolute sample number of the last matched sample (set before `wait()` returns). |
| `self.matched` | Tuple of bools, one per condition in a list-cond, saying which matched (`None` for a single-dict cond). |
| `self.put(ss, es, out_id, data)` | Emit on a registered output stream. `data` shape per type: ANN = `[class_idx, [text_long, ..., text_short]]`; PYTHON = arbitrary Python object (routed to stacked decoders); BINARY = `[class_idx, bytes]`; META = number. |
| `self.register(output_type, proto_id=..., meta=...)` | Returns an int output id. |
| `self.has_channel(idx)` | bool: is optional channel idx connected. |
| `self.options` | dict pre-populated by the host (defaults merged with user values) before `start()`. |

Verified usage counts across all 132 `pd.py` files: `srd.*` = `Decoder` 131, `OUTPUT_ANN` 129,
`SRD_CONF_SAMPLERATE` 50, `OUTPUT_PYTHON` 26, `OUTPUT_BINARY` 24, `OUTPUT_META` 6,
`OUTPUT_LOGIC` 2. Instance members: `put` 588, `samplenum` 537, `register` 187, `wait` 149,
`matched` 80, `has_channel` 25.

### 1.5 Packet semantics (what a consumer receives)

- **ANN**: `[ss, es, class_idx, [texts...]]`, texts ordered longest-first (UI picks by zoom).
  `annotation_rows` says which class indexes group into which row.
- **PYTHON**: machine-readable payload documented per-PD in each `pd.py` docstring (i2c:
  `['ADDRESS WRITE', 0x50]`; spi: `['DATA', mosi, miso]`; uart: `['DATA', (rxtx, (value, bits))]`).
  This is the decoded-bytes-table feed and the stacking interface.
- **BINARY**: `[class_idx, bytes]`, protocol payload export.
- **META**: numeric measurements (e.g. bitrate), keyed by the `meta=(type,name,descr)` label.

## 2. Reference architecture: how the C runtime drives PDs (basis for the shim)

The libsigrokdecode C API sequence (`libsigrokdecode.h`): `srd_init(path)` (searchpaths:
compile-time `DECODERS_DIR`, XDG data dirs, env `SIGROKDECODE_DIR`, env `SIGROKDECODE_PATH`,
plus `path`) → `srd_decoder_load` → `srd_session_new` → `srd_inst_new` +
`srd_inst_channel_set_all` (PD channel idx → bit offset in sample unit) + `srd_inst_option_set`
→ `srd_inst_stack` (stacking; top's `decode(ss,es,data)` is invoked from bottom's
`put(OUTPUT_PYTHON)`, verified in `type_decoder.c` Decoder_put) → `srd_session_metadata_set`
→ `srd_pd_output_callback_add` → `srd_session_start` → `srd_session_send(abs_start, abs_end,
inbuf, inbuflen, unitsize)` chunks (samplenums absolute, ordered, no gaps; buffer borrowed, not
copied) → `srd_session_send_eof` → `srd_session_destroy` → `srd_exit`.

Mechanics relevant to the shim: each bottom instance gets a worker thread; `wait()` blocks on a
condvar until the host pushes the next buffer; the runtime evaluates the wait-terms against
packed samples (bit extraction via the channel map), sets `samplenum`/`matched`, returns the
pinvalue tuple. PD exceptions are caught and logged per instance; `EOFError` from a PD is clean
termination. Deps of the C lib: glib-2.0 ≥ 2.34, Python 3 (it embeds CPython).

pdhost does not link the C library; it reproduces the *behavior* of the wait-term matcher in
pure Python from the documented interface (see §4.3 and licensing note in §5).

## 3. Decoder corpus & LogicPort-relevant families

- Discovery: a PD = package dir whose `__init__.py` exports `Decoder`. Search path is
  configurable (pdhost: explicit `pd_dir` argument). `common/` shared helpers need the
  **whole** `decoders/` tree on the import path, not per-PD copies.
- 132 PDs ship upstream. Relevant to a 34-channel bench analyzer (all need 1–4 channels;
  constraint is count, not pin identity):

| Decoder (`id`) | need | Optional | Notes |
|---|---|---|---|
| `i2c` | scl, sda (2) |, | stacks: eeprom24xx, ds1307, atsha204a, enc28j60, i2cdemux, i2cfilter... |
| `spi` | clk (1) | miso, mosi, cs | all data pins optional; wordsize/cpol/cpha options |
| `uart` |, (0) | rx, tx | timebase decode; needs samplerate + baudrate option; `rs232` is a near-dup |
| `can` | can_rx (1) |, | bitrate + sample-point options |
| `onewire_link` | owr (1) |, | `onewire_network` stacks on top |
| `ps2` | clk, data (2) |, | keyboard/mouse |
| `i2s` | sck, ws, sd (3) |, | |
| `jtag` | tdi, tdo, tck, tms (4) | trst, srst, rtck | |
| `usb_signalling` | dp, dm (2) |, | low/full-speed electrical layer |
| `dmx512` / `midi` / `cec` | 1 |, | uart-like single-line |
| `edid`, `sdcard_*`, ... | 0 (stacked consumers) |, | consume another PD's PYTHON output |

## 4. The pdhost library

### 4.1 Data model

Producers give pdhost **named channels**. Two producer shapes both occur (the plugin's column
model is one of them), so both are first-class:

- `Signal.from_samples(values)`, uniform per-sample states (array-like of 0/1/None).
- `Signal.from_edges(times, values)`, change events: sample times + the value after each
  change (the RLE/edge-timestamp model).

The **sample grid** is what `wait()` actually walks. `SampleStream` carries channels plus a
grid policy:

- `clock(rate)`, producer-declared true sample clock (best fidelity; state-mode captures).
- `uniform(rate)`, synthesized uniform grid at a given rate (default: computed ≥ 4× the
  fastest observed edge period if the producer gives no clock).
- Grid table `t(samplenum)` is retained so packets can carry both sample numbers and times.

PD channels bind to stream channels **by name**; the declaration index → name map is the
channel-binding model (§4.4).

### 4.2 Public API sketch

```python
import pdhost

# discover + inspect
pd = pdhost.load_decoder(pd_dir="decoders", decoder_id="i2c")
# -> DecoderInfo(id, name, longname, desc, license, tags, inputs, outputs,
#                channels: [ChannelDecl(id, name, desc, index, needed)],
#                options: [OptionSpec(id, desc, default, values)],
#                annotations: [(class_id, label)], annotation_rows, binary, api_version)

# build a stream (named channels; either producer shape)
stream = pdhost.SampleStream(
    grid=pdhost.clock(1_000_000),                    # or pdhost.uniform(...)
    channels={
        "SCL": pdhost.Signal.from_edges(times=[...], values=[...]),
        "SDA": pdhost.Signal.from_samples([...]),
    })

# bind + configure
inst = pd.bind(channel_map={"scl": "SCL", "sda": "SDA"},   # PD channel id -> stream channel name
               options={"address_format": "shifted"})

# run: iterate packets, or collect
for pkt in inst.run_iter(stream):
    pkt.kind            # "ann" | "python" | "binary" | "meta"
    pkt.ss, pkt.es      # sample numbers
    pkt.start_time, pkt.end_time   # resolved via the grid table
    pkt.cls, pkt.class_id, pkt.texts   # ann: class index, declared id ('start'), texts long->short
    pkt.data            # python / binary / meta payload

result = inst.run(stream)   # -> RunResult(packets, by_kind, annotations_by_row, ...)

# stacks
top = pdhost.load_decoder(pd_dir, "eeprom24xx").bind()   # no channels; consumes i2c PYTHON
inst.stack(top)
for pkt in inst.run_iter(stream):   # now emits both i2c and eeprom24xx packets
    ...
```

Error model: unknown decoder / bad api_version / unmapped needed channel / option not in
`values` raise typed exceptions at bind time; a PD exception during `decode()` aborts that
instance and surfaces in `RunResult.errors` (never crashes the consumer).

### 4.3 Package skeleton

```
pdhost/
  __init__.py          # public API re-exports, __version__
  api.py               # DecoderInfo, ChannelDecl, OptionSpec, Packet, RunResult dataclasses
  sigrok_shim.py       # the 'sigrokdecode' module substitute: Decoder base class + constants
  term.py              # wait-condition term matcher over a grid (h/l/r/f/e/n + skip)
  grid.py              # SampleStream/Signal; grid synthesis; samplenum<->time tables
  loader.py            # PD discovery: scan pd_dir, importlib, api_version 3 gate, attr reads
  stack.py             # stack composition: lower put(PYTHON) -> upper decode(ss, es, data)
  collector.py         # packet sinks: list, iterator, callback
  runner.py            # optional out-of-process mode (same engine, JSON over pipes)
  py.typed
tests/
  test_term.py         # term matcher vs golden wait() traces
  test_grid.py         # edges->uniform synthesis, skip accounting
  test_i2c_smoke.py    # real capture -> expected annotation stream
  test_stack.py        # i2c -> eeprom24xx
examples/
  run_i2c.py
```

`sigrok_shim.py` notes: registers a synthetic module named `sigrokdecode` in `sys.modules`
before importing a PD; implements `wait()` synchronously over the grid (no threads needed in a
batch host); raises `EOFError` at stream end; unconnected optional channels report `0xff` in
pinvalues; `matched` is `None` for single-dict conditions, matching upstream byte-for-byte
where observable.

Performance: the term matcher evaluates level terms (`h/l`) only at edges of the referenced
channel (nothing changes between edges) plus `skip` bookkeeping; measure before further
optimizing, the i2c PD deliberately tolerates undersampling and wants every sample.

### 4.4 Channel-binding model (how decoder channels map to device pins)

1. `pd.channels` / `pd.optional_channels` give the PD's declared channels with indexes.
2. The consumer binds each declared channel id to a **stream channel name**
   (`channel_map={"scl": "SCL"}`); unmapped needed channels are a bind-time error; unmapped
   optional channels are reported `0xff` to the PD (`has_channel()` → False).
3. The producer decides what a stream channel *is*, for the LogicPort plugin, a captured
   column (D0–D31/CLK); for a file importer, a CSV column; for tests, a synthesized signal.
   Nothing in pdhost knows about device pins: the binding surface is names only.
4. Declarations (`channels`, `options`) drive UI: pin pickers and option forms are generated
   from `DecoderInfo`, no per-decoder UI code.

## 5. Licensing: the library's own decision (evidence-based)

**Ground truth (this analysis):**

1. `import sigrokdecode as srd` resolves to an 88-line C stub: one type + 6 int constants,
   no protocol logic (`module_sigrokdecode.c`). The matching state machine lives in
   `type_decoder.c` but its *interface* is fully documented (docstrings + public wiki).
2. The PD corpus imports only: `srd`, sibling `common/` helpers, and the stdlib
   (`re`, `struct`, `math`, `collections`, ...), zero third-party packages, zero other
   calls into the C library. Verified by full import tally over all 132 `pd.py` files.
3. Dependency direction at runtime: **PD → shim**. A PD subclasses our `Decoder` and imports
   our module; pdhost reaches PDs only through the documented, generic package-layout
   contract (import + class attributes + `decode()`/`decode(ss,es,data)`).

**License options for pdhost:**

- **MIT core, in-process PD loading, corpus never bundled (recommended).** The library is a
  generic plugin host speaking a published interface. It never ships or installs PD files;
  the PD directory is user-supplied (their copy of sigrok's `decoders/`, kept as an upstream-
  faithful tree with intact license headers, a separate work). This is the established
  plugin-host model (pytest/Sphinx/Home Assistant hosts load third-party-licensed plugins
  under the same reasoning), and the runtime dependency direction (PD depends on pdhost, not
  the reverse) supports the works remaining separate. If a distributor later bundles pdhost
  **and** the sigrok corpus into one artifact, that artifact is a combined distribution and
  the corpus side must carry its GPL notices, the policy text in the README says so.
- **Out-of-process runner mode (optional, for hard isolation).** `pdhost.runner` spawns
  `python -m pdhost.runner` with the same engine; the parent consumes JSON packets over
  pipes. Use when a deployment needs zero GPL code in the consumer's address space. Same
  library API on both sides; costs serialization overhead.
- **Rejected: MIT library that bundles the sigrok corpus**, a combined distribution; GPL
  obligations would attach to the artifact.
- **Rejected: linking/embedding libsigrokdecode in-process**, makes the host GPL-encumbered
  under the FSF reading; also drags glib and a second embedded CPython into the process.

**Clean-room discipline for `sigrok_shim.py` + `term.py`:** write from the documented
interface (docstrings quoted in §1.4, upstream wiki), not from `type_decoder.c` sources; keep
their git history free of `type_decoder.c` transcription. The interface (names, term alphabet,
return shapes) is not copyrightable; the C implementation is.

## 6. Open questions

1. **Grid fidelity**: does the LogicPort capture preserve the true sample clock (state mode)
   or only edge timestamps (timing mode)? Decides `clock()` vs `uniform()` defaults and

**RESOLVED (2026-10-12, from the device protocol decode)**: in timing mode the wire run-length counts are exact multiples of the sample period. This is fixed-rate sampling: each run column stores the count of unchanged periods. The RLE-expanded columns therefore ARE the real uniform sample grid: use the `clock(rate)` policy and materialize each column exactly `count` times at 1/rate spacing. UART-style timebase decode on timing-mode captures is exact up to the device's ±0.005% timebase accuracy. In state mode the columns are external-clock events, not a uniform grid. Use the `uniform(rate)` policy (virtual rate >= 4x fastest edge) and warn or refuse timebase-sensitive decoders on state-mode captures.
   whether uart-style timebase decode is trustworthy (baud error accumulates on synthesized
   grids). Needs the acquisition lane's answer.
2. **Stacking scope for v1**: i2c → eeprom24xx is the flagship pair; are deeper stacks
   (usb_signalling → usb_packet, uart → modbus-family) needed at first release?
3. **Corpus distribution**: point consumers at a sigrok checkout, or publish a separate
   `sigrok-decoders` data package (upstream headers intact, its own license metadata)?
4. **Undersampling UX**: surface a samples-per-bit estimate per (decoder, options, grid)
   before decoding; several PDs (CAN, USB) are unforgiving, i2c is deliberately tolerant.
5. **Version pinning**: PD api_version is 3 today; pin the shim to upstream 0.5.x behavior
   and add a compatibility test against a small corpus snapshot in CI.
