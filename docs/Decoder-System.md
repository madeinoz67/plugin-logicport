# LogicPort Decoder System (Interoperability Record)

> Source: `libsigrokdecode` (sigrok project), shallow clone at `sigrokdecode-src/`.
> The upstream project license is GPLv3 or later. Most decoder files carry GPLv2-or-later headers.
> Register: ASD-STE100 Simplified Technical English. Technical names stay as is.
> Status: work in progress. Each section lists its evidence.
> This doc records the facts of an external open-source system. It does not reproduce code.

## 1. Purpose

The LogicPort plugin must show decoded protocol traffic. The sigrok project gives a large set
of open protocol decoders. This doc records how those decoders work, so the team can host them
over LogicPort data. A separate design note, `sigrok-decoder-integration.md`, gives the library
design (`pdhost`). This doc gives the system facts only.

## 2. What a protocol decoder is

A protocol decoder (PD) is a small Python package. The package turns a digital sample stream
into protocol events. Each PD knows one protocol: I2C, SPI, UART, CAN, and 128 more.

A PD has two files. `__init__.py` has a docstring and one import of the Decoder class.
`pd.py` holds the class `Decoder`. The class must be a subclass of the host base class.
The host accepts only PDs with `api_version == 3`.

The host imports the package and reads class attributes. There is no metadata file. The
class attributes are the metadata.

Evidence: `sigrokdecode-src/decoder.c` (load gate, api version check);
`sigrokdecode-src/decoders/i2c/__init__.py`; `sigrokdecode-src/decoders/i2c/pd.py`.

## 3. Decoder declaration surface

The `Decoder` class declares its needs as plain class attributes. Table 1 lists the surface.

Table 1: Decoder class attributes

| Attribute | Type | Meaning |
|---|---|---|
| `api_version` | int | Must be 3. The load gate. |
| `id` | string | Registry key, for example `'i2c'`. |
| `name`, `longname`, `desc`, `license`, `tags` | strings | Display text and license tag. |
| `inputs` | tuple of strings | Data types the PD reads. `'logic'` = raw samples. Or another PD's `outputs` value. |
| `outputs` | tuple of strings | Data types the PD emits. |
| `channels` | tuple of channel dicts | Mandatory channels. Each dict has `id`, `name`, `desc`. The tuple position is the channel index. PD code uses the index in wait conditions. |
| `optional_channels` | tuple of channel dicts | Channels that can stay unconnected. Unconnected channels read as `0xff`. |
| `options` | tuple of option dicts | User settings. Each dict has `id`, `desc`, `default`, `values`. The host builds the options dict before `start()`. |
| `annotations` | tuple of `(id, label)` pairs | Annotation classes. The pair position is the class number. PD code uses the number in `put()`. |
| `annotation_rows` | tuple of `(row id, label, class numbers)` | Row groups for display. |
| `binary` | tuple of `(id, label)` pairs | Binary output classes. |

Example: the I2C PD declares channels `scl` and `sda`. It declares 11 annotation classes, for
example `('start', 'Start condition')` at class number 0. It declares 3 annotation rows.

Evidence: `sigrokdecode-src/decoders/i2c/pd.py`, lines 69 to 110.

## 4. The decode() protocol

The `decode()` method is the main loop of a bottom decoder. The method takes no arguments.
It runs one time. It loops until an exception stops it. The loop body asks the host for the
next sample that matches a condition. This ask is the `wait()` call.

### 4.1 wait() patterns

The argument of `wait()` is a condition. Table 2 lists the patterns.

Table 2: wait() condition patterns

| Pattern | Example | Match rule |
|---|---|---|
| Dict of channel terms | `{0: 'r'}` | All terms must be true at the same sample. |
| List of dicts | `[{0: 'r'}, {0: 'h', 1: 'f'}]` | The first dict that matches at a sample. At least one must match. |
| Empty or absent | `self.wait()` | The next sample always matches. |
| Skip term | `{'skip': 5}` | Advance 5 samples, then match. The key is the string `'skip'`. |

Table 3 lists the term alphabet for channel terms.

Table 3: wait() channel terms

| Term | Meaning |
|---|---|
| `'h'` | Channel high at the sample. |
| `'l'` | Channel low at the sample. |
| `'r'` | Rising edge at the sample. |
| `'f'` | Falling edge at the sample. |
| `'e'` | Either edge at the sample. |
| `'n'` | No edge at the sample. |

After a match, the host sets two attributes on the PD instance. `self.samplenum` gets the
matched sample number. `self.matched` gets a tuple of booleans. One boolean per condition in
the list form. A single dict condition gives `self.matched = None`. The `wait()` call returns
the pin values at the matched sample. The tuple has one value per declared channel, in
declaration order. An unconnected optional channel gives the value `0xff`.

The host raises `EOFError` in `wait()` when the sample stream ends. PDs use this signal to end
the loop. This end is a sanctioned stop, not an error.

Evidence: `sigrokdecode-src/type_decoder.c`, Decoder_wait (lines 966 to 1135);
`sigrokdecode-src/instance.c` (EOFError accepted at decode end).

### 4.2 Two decode() shapes

There are two decode() shapes. A **bottom** decoder gets samples from the host. A **top**
decoder gets packets from the decoder below it. Table 4 compares the shapes.

Table 4: decode() shapes

| Shape | Signature | Data source | wait() use |
|---|---|---|---|
| Bottom | `decode(self)` | Host sample feed | Allowed |
| Top (stacked) | `decode(self, ss, es, data)` | Lower PD packets | Not allowed |

The host starts the bottom decoder's `decode(self)` one time. A top decoder's
`decode(self, ss, es, data)` runs on each packet from the decoder below. The data object is
the exact Python object that the lower PD put.

Evidence: `sigrokdecode-src/decoders/eeprom24xx/pd.py` (stacked shape);
`sigrokdecode-src/instance.c`, di_thread (bottom call).

## 5. Annotation output

A PD emits packets with `self.put(ss, es, output_id, data)`. The arguments are the start
sample number, the end sample number, a registered output stream id, and the payload. The PD
gets output ids from `self.register(output_type)` in `start()`. Table 5 lists the output types.

Table 5: Output types and payloads

| Type | Payload shape | Consumer |
|---|---|---|
| `OUTPUT_ANN` | `[class number, [texts, longest first]]` | UI overlays. Rows come from `annotation_rows`. |
| `OUTPUT_PYTHON` | Any Python object | Stacked PDs, and a decoded-data table. |
| `OUTPUT_BINARY` | `[class number, bytes]` | Payload export. |
| `OUTPUT_META` | A number | Measurements, for example bitrate. |
| `OUTPUT_LOGIC` | Re-derived sample data | Rare. Sends samples back to the host. |

Each PD documents its PYTHON packet format in its `pd.py` docstring. Examples: I2C sends
`['ADDRESS WRITE', 0x50]`. SPI sends `['DATA', mosi_value, miso_value]`. These packets carry
the decoded values. A decoded-bytes table reads them. The sample span `ss` to `es` marks the
screen span. The host maps sample numbers to capture time.

Evidence: `sigrokdecode-src/type_decoder.c`, Decoder_put (lines 399 to 562);
`sigrokdecode-src/decoders/i2c/pd.py` docstring.

## 6. Decoder stacks

A stack puts decoders in a chain. The output of one decoder becomes the input of the next.
The host checks one rule: an `inputs` value of the top PD must match an `outputs` value of
the bottom PD. A mismatch gives a warning, not a hard error.

The chain works through `OUTPUT_PYTHON` packets only. When a bottom PD calls
`put()` on its PYTHON stream, the host calls `decode(ss, es, data)` on each PD above it. The
same data object moves up. No packet marshalling happens.

Example: the I2C PD emits PYTHON packets with type tags. The eeprom24xx PD consumes them. It
reads the address and data bytes. It emits annotations for the memory content. LogicPort users
see one trace with two annotation rows.

Evidence: `sigrokdecode-src/type_decoder.c` (Decoder_put, PYTHON case);
`sigrokdecode-src/instance.c`, srd_inst_stack (lines 521 to 568).

## 7. Host data feed

The host feeds samples to the decoders. The feed rules are strict:

1. Feed sample chunks in order. Do not skip samples. Do not repeat chunks.
2. Use absolute sample numbers. Start at 0. Continue with no gaps.
3. Pack mapped channels bit-packed into a sample unit. Give the unit size in bytes.
4. Keep the buffer alive until the feed call returns.

The runtime pairs each bottom decoder with a worker thread. The thread runs `decode()`. The
`wait()` call blocks on a condition variable. Each new chunk wakes the thread. The runtime
checks the wait terms against each sample. On a match it sets `samplenum` and `matched`, and
returns pin values.

After the last chunk, the host signals end of stream. Pending `wait()` calls raise `EOFError`.
The host then calls the optional `flush()` hook of each PD.

Evidence: `sigrokdecode-src/instance.c`, srd_inst_decode (lines 1185 to 1257);
`sigrokdecode-src/session.c`, srd_session_send docstring (lines 262 to 279).

## 8. Channel binding

A PD declares channels by index. The capture gives device pins. The host maps one to the
other. The map direction is: PD channel index to bit position in the sample unit.

Example: the I2C PD declares `scl` at index 0 and `sda` at index 1. The user assigns LogicPort
column D0 to `scl` and column D1 to `sda`. The sample unit packs bit 0 = `scl`, bit 1 = `sda`.

The rules:

- The host must map all mandatory channels. Unmapped optional channels are legal.
- The pin assignment is a user choice. The PD does not know pin names.
- `self.has_channel(idx)` tells the PD if an optional channel is connected.
- A PD can run with mandatory channels only. Example: SPI needs only `clk`. The PD handles
  missing optional pins with `has_channel()` checks and `0xff` reads.

Evidence: `sigrokdecode-src/instance.c` (srd_inst_channel_set_all);
`sigrokdecode-src/type_decoder.c`, get_current_pinvalues (lines 701 to 736);
`sigrokdecode-src/decoders/spi/pd.py`, lines 86 to 93.

## 9. Licensing facts

The sigrok decoder system is free software. Table 6 lists the facts.

Table 6: License facts

| Fact | Detail |
|---|---|
| Project license | GPLv3 or later. File `COPYING` holds the GPLv3 text. |
| Decoder files | Most carry GPLv2-or-later headers. The class attribute `license` says `'gplv2+'`. |
| Library code | The C files carry GPLv3+ headers. |
| What GPL permits | Use, study, change, and share, under the GPL terms. |
| What GPL needs | Works that distribute GPL code must pass the GPL terms to recipients. |
| Host model | The decoder files are a separate work. A host that loads user-supplied decoder files does not copy them. |

The host plan keeps the decoder files separate. The plugin or library never bundles decoder
files. The user supplies the decoder files. The design note records the full options analysis.

Evidence: `sigrokdecode-src/COPYING` (GPLv3 text); PD file headers (for example
`decoders/i2c/pd.py`, lines 1 to 18); `COPYING` file size 34.3 KB (GPLv3 full text).

## 10. Reference list

| Item | Path |
|---|---|
| PD API contract | `sigrokdecode-src/decoders/i2c/pd.py`, `spi`, `uart`, `can`, `eeprom24xx` |
| Decoder base class | `sigrokdecode-src/type_decoder.c` |
| sigrokdecode module | `sigrokdecode-src/module_sigrokdecode.c` |
| Session feed | `sigrokdecode-src/session.c`, `instance.c` |
| Load gate | `sigrokdecode-src/decoder.c` |
| Public C API | `sigrokdecode-src/libsigrokdecode.h` |
| Design note | `docs/sigrok-decoder-integration.md` |
