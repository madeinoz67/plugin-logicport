# LogicPort LA1034 technical reference

> A single implementation reference for the Intronix LogicPort LA1034 logic
> analyser. It carries what a client must do to use every function of the device.
> Each statement carries how it is known:
>
> | Mark | Meaning |
> | --- | --- |
> | **MEASURED** | verified on hardware with a working client |
> | **DECODED** | read from the vendor application binary, not yet seen on hardware |
> | **UNKNOWN** | not established. See the section at the end |
>
> The dated research record is in `LogicPort-Protocol.md`. That file holds the
> history, including readings later retracted. This file holds the current model.

---

## 1. The device

A 34 channel logic analyser with a 2048 sample buffer, a trigger engine, a
frequency counter on each input, and two sampling modes. It presents a USB
interface through an FTDI bridge and a field programmable gate array that must be
loaded with a bitstream before it does anything.

| Property | Value |
| --- | --- |
| USB vendor | `0x0403` FTDI |
| USB product | `0xDC48` |
| Product string | `LogicPort Logic Analyzer` |
| Interface | FTDI D2XX, not a virtual serial port |
| Channels | D0 to D31, plus CLK1 and CLK2 |
| Buffer | 2048 samples |
| Base clock | 10 MHz, `MEASURED` through the rate table |

The FPGA holds no configuration after a USB replug. A client must load a
bitstream each time the device appears.

### The LED

The LED reports device health, not configuration.

| LED | Meaning |
| --- | --- |
| lit | the device answers. It may hold a bitstream or none |
| dark | the device is in a fault state. Replug it |

A healthy device with no bitstream loaded shows the LED **lit**. A dark LED means
a write put the device into a fault state, and only a replug clears it.

**Never write `0x61` to register `0x00100000`.** That single write is the measured
cause of the fault state. A client has no reason to send it.

---

## 2. Transport

### Connection

```
FTDI D2XX, VID 0x0403, PID 0xDC48
baud rate   3000000
latency     4 ms
```

Three Mbaud loads a one megabyte bitstream in about 1.4 seconds. A baud rate of
460800 takes about 23 seconds, so the rate matters.

### Frame grammar

Three frame kinds. Every frame starts with a five byte header.

| Byte | Meaning |
| --- | --- |
| 0 | opcode. `0xC1` write, `0xC2` read, `0xC3` page select |
| 1 | register number, low byte |
| 2 | register number, high byte |
| 3 | length, low byte |
| 4 | length, high byte |

```
C1 <reg lo> <reg hi> <len lo> <len hi> <payload...>     write
C2 <reg lo> <reg hi> <count-1 lo> <count-1 hi>          read
C3 <page lo> <page hi> 00 00                            page select
```

The register field is the low 16 bits of a 32 bit device address. The page select
frame carries bits 16 and above of that same address.

### Length fields

For a write, the length field holds `payload length - 1`.

For a read, the length field holds `bytes wanted - 1`. A read of one byte sends a
length field of zero. A read of 2048 bytes sends 2047.

### Replies

| Frame | Reply | Shape |
| --- | --- | --- |
| `C1` | 3 bytes | `C1 <counter> <counter>` |
| `C3` | 3 bytes | `C3 <counter> <counter>` |
| `C2` | `wanted + 3` bytes | three overhead bytes then the payload |

A read of one byte returns four bytes. The payload starts at index 3, so the
status byte of a one byte read is reply byte 3.

The counter increments by one per command. A gap in the counter detects a lost or
duplicated frame.

### Page select

Send a page select before the first access in a page, and after any
resynchronisation. The client caches the current page, and the cache starts
invalid.

| Address range | Page | Frame |
| --- | --- | --- |
| `0x00100000` | `0x0010` | `C3 10 00 00 00` |
| `0x01000000` | `0x0100` | `C3 00 01 00 00` |
| `0x00200000` | `0x0020` | `C3 20 00 00 00` |
| `0x00400000` | `0x0040` | `C3 40 00 00 00` |
| `0x02000000` | `0x0200` | `C3 00 02 00 00` |
| `0x20000000` | `0x2000` | `C3 00 20 00 00` |
| `0x40000000` | `0x4000` | `C3 40 00 00 00` |

**Retry a page select that gets no reply.** A silent failure leaves the cache
stale, and every later read then goes to the wrong page and returns `0xFF` as if
it were data. This is the single most misleading failure mode in the protocol.

### Resynchronisation

After a bitstream load, and after any error, send this flush:

```
purge the transmit and receive buffers
write 65535 bytes of 0x00
wait 50 ms
write 30000 bytes of 0x00
write one 0x01
wait 120 ms
purge again
```

Then perform eight one byte reads before any configuration write.

---

## 3. Register map

Addresses are 32 bit. The page is `address >> 16` and the register field is the
low 16 bits.

### Command bank, address base `0x00100000`, page `0x0010`

| Address | Width | Meaning |
| --- | --- | --- |
| `0x00100000` | 1 | status byte |
| `0x00100002` | 1 | trigger enable |
| `0x00100003` | 1 | arm. 1 captures, 0 halts |
| `0x00100004` | 2 | trigger position A, pre-fill count |
| `0x00100006` | 2 | trigger position B, post-fill count |
| `0x00100008` | 2 | threshold digital to analogue word |
| `0x0010000A` | 1 | phase exit, pre-fill |
| `0x0010000B` | 1 | phase exit, post-fill |
| `0x0010000C` | 1 | phase exit, force trigger |
| `0x0010000D` | 1 | tail frame. Write zero |
| `0x0010000F` | 2 | loaded image identity, read only |

### Mode bank, address base `0x01000000`, page `0x0100`

| Address | Width | Meaning |
| --- | --- | --- |
| `0x01000000` | 1 | mode byte |
| `0x01000001` | 1 | auxiliary byte. Always zero |
| `0x01000002` | 1 | trigger enable companion |
| `0x01000003` | 5 | sample mask A, 34 channels |
| `0x01000008` | 5 | sample mask B, 34 channels |

### Trigger term banks, pages `0x0020` and `0x0040`

Term slot 0 uses base `0x00200000` and slot 1 uses base `0x00400000`. Both carry
the same offsets.

| Offset | Width | Meaning |
| --- | --- | --- |
| `+0x00` | 5 | pattern plane A |
| `+0x05` | 5 | pattern plane B |
| `+0x0A` | 4 | occurrence count |
| `+0x0E` | 4 | second parameter |
| `+0x12` | 1 | value and term mode |
| `+0x14` | 1 | flag |
| `+0x16` | 1 | term class. 3 means edge |
| `+0x17` | 1 | term type |
| `+0x18`, `+0x19` | 1 | control and match byte |
| `+0x1A`, `+0x1F`, `+0x24` | 4 | value low, middle and high |
| `+0x29` | 5 | edge plane A |
| `+0x2E` | 5 | edge plane B |

### Sample area, address base `0x02000000`, page `0x0200`

| Address | Width | Meaning |
| --- | --- | --- |
| `0x02000000` | 2 | cursor A |
| `0x02000100` | 2 | cursor B |
| `0x02001000` | ring | channels D0 to D7, plane 0 |
| `0x02002000` | ring | channels D8 to D15, plane 1 |
| `0x02003000` | ring | channels D16 to D23, plane 2 |
| `0x02004000` | ring | channels D24 to D31, plane 3 |
| `0x02005000` to `0x02008000` | ring | the same four planes for the second page in byte mode |
| `0x02009000` | ring | flags plane, and the two clock inputs |

A plane window is 4096 bytes and holds two mirrored copies of a 2048 byte ring.

### Clock bank, address base `0x20000000`, page `0x2000`

| Address | Width | Meaning |
| --- | --- | --- |
| `0x20000000` | 1 | clock byte A |
| `0x20000001` | 1 | clock byte B |

### Frequency counter, address base `0x40000000`, page `0x4000`

| Address | Width | Meaning |
| --- | --- | --- |
| `0x40000000` | 1 | channel select, read and write |
| `0x40000001` | 4 | count, little endian |
| `0x40000005` | 1 | status |

---

## 4. Bitstream and the eight images

The device needs a bitstream before it does anything. The file is `LogicPort.CCF`
and it holds **eight separate images**, each a complete configuration.

### The file layout

The first line of the file is ASCII: nine decimal numbers separated by commas.
XOR each one with `0x916662` to recover its value.

| Field | Meaning |
| --- | --- |
| values 0 to 7 | the eight image lengths, in units of 16 bytes |
| value 8 | an XOR checksum of the first eight, advisory |

The body starts after the first newline and the eight images tile it exactly. The
body of the shipped file is 8387696 bytes.

| Image | Length |
| --- | --- |
| 0 | 1057840 |
| 1 | 1044512 |
| 2 | 1046896 |
| 3 | 1047328 |
| 4 | 1046512 |
| 5 | 1054992 |
| 6 | 1031232 |
| 7 | 1058384 |

### The transform

Exchange two 4096 byte pages before streaming. **Exchange the first 4096
bytes of the body with the next 4096 bytes.** Without this the device never
asserts DONE.

Stream the image bytes unchanged. The body uses only the byte values `0x04` to
`0x07`, and a client must not decode or expand them.

### The eight images, one entry each

The file holds **eight** images, indexed 0 to 7. A client loads exactly one. The
table answers what each is, when to load it, and how to prove it took.

Offsets are from the start of the body, which begins at absolute file offset 73.

| Index | Body offset | Length | Selected by | Sampling clock | Edge trigger |
| --- | --- | --- | --- | --- | --- |
| 0 | 0 | 1057840 | sample mode 2 | internal | no |
| 1 | 1057840 | 1044512 | sample mode 4, `clkState` 0 | CLK1 | no |
| 2 | 2102352 | 1046896 | sample mode 4, `clkState` 2 | CLK2 | no |
| 3 | 3149248 | 1047328 | sample mode 4, `clkState` 1 | CLK1 | no |
| 4 | 4196576 | 1046512 | sample mode 4, `clkState` 3 | CLK2 | no |
| 5 | 5243088 | 1054992 | sample mode 3 | internal | no |
| 6 | 6298080 | 1031232 | sample mode 1 | internal | **yes** |
| 7 | 7329312 | 1058384 | sample mode 1, when a further flag is set | internal | **yes** |

### What each image is for

| Index | Use it when |
| --- | --- |
| 7 | you want a **triggered capture on the internal clock**. This is the image the bench used for all working captures |
| 6 | the same as 7. Both belong to sample mode 1 and both trigger |
| 1 | you want to sample on an edge of an external clock on **CLK1** |
| 3 | the same as 1, for the other edge or source choice of `clkState` 1 |
| 2 | you want to sample on an edge of an external clock on **CLK2** |
| 4 | the same as 2, for `clkState` 3 |
| 0 | sample mode 2, a timing mode. Its distinct purpose was never established |
| 5 | sample mode 3, a timing mode. Its distinct purpose was never established |

Every image answers commands and fills the buffer. Only 6 and 7 fire the edge
trigger. `MEASURED`.

### How to load an image

```
1  open the device, baud 3000000, latency 4 ms
2  if the device already holds an image, unconfigure it:
      framed write of 0x00 to register 0x00100000, then confirm DONE is low
3  enter bitbang mode, mask 0x07
4  write the byte 0x00
5  confirm DONE reads low. If it stays high the stream is a no-op, so stop
6  write the byte 0x04
7  confirm INIT goes high within 1000 ms
8  stream the image in 10000 byte chunks, from the body offset in the table
9  confirm DONE goes high within 1000 ms
10 leave bitbang mode, then flush and prime eight reads
```

Extract the image like this:

```python
def image_bytes(ccf_path: str, index: int) -> bytes:
    raw = open(ccf_path, "rb").read()
    newline = raw.index(b"\n")
    lengths = [int(x) ^ 0x916662 for x in raw[:newline].decode().split(",")[:8]]

    body = raw[newline + 1:]
    offset = sum(n * 16 for n in lengths[:index])
    image = bytearray(body[offset:offset + lengths[index] * 16])

    # exchange the first two 4096 byte pages, or DONE never asserts
    image[0:4096], image[4096:8192] = image[4096:8192], image[0:4096]
    return bytes(image)
```

### How to validate that the right image took

Read two bytes from register `0x0010000F`. The low byte holds the image index
with bit 4 set.

| Image | Register reads |
| --- | --- |
| 0 | `0x10` |
| 1 | `0x11` |
| 2 | `0x12` |
| 3 | `0x13` |
| 4 | `0x14` |
| 5 | `0x15` |
| 6 | `0x16` |
| 7 | `0x17` |

**Read this after every load and before every capture.** A load that never took
leaves the previous index in place, so a capture can look healthy while running
the wrong image. This is how the eight image behaviour matrix above was built: one
image per run, with the index read back each time. `MEASURED`.

Two further checks a client can make:

| Check | How |
| --- | --- |
| the image is resident | register `0x0010000F` low nibble equals the index |
| the image runs | a capture completes, or pre-fill progresses past `0x00` |

### What is unknown about the images

| Question | State |
| --- | --- |
| which image 6 versus 7 the vendor picks | both trigger. The choice depends on a flag that was never traced |
| the distinct purpose of image 0 | known to be sample mode 2. The mode name was not recovered |
| the distinct purpose of image 5 | known to be sample mode 3. The mode name was not recovered |
| what distinguishes the two images in each clock pair | the decode gives `clkState`, which pairs a source with an edge. Which of the pair is the rising edge was not measured |
| whether the images differ in rate | no. Three rows of the clock table gave three rates on one image, so rate is set by the clock bytes and not by the image |

### Loading all eight in one stream does not work

Streaming the whole body as one 8387696 byte burst succeeded once and faulted the
device every time after. **Load one image.** Treat the eight as alternatives and
not as parts of one image. `MEASURED`.

## 5. Configuration

This is the frame list that produces a working capture. Every step carries a measurement.

### Order

```
page 0x2000   clock byte A, 1 byte, to 0x20000000
page 0x2000   clock byte B, 1 byte, to 0x20000001
page 0x0100   mode byte, 1 byte, to 0x01000000
page 0x0100   0x00, 1 byte, to 0x01000002
page 0x0100   mask A, 5 bytes, to 0x01000003
page 0x0100   mask B, 5 bytes, to 0x01000008
page 0x0100   auxiliary 0x00, 1 byte, to 0x01000001
page 0x0010   0x00, 1 byte, to 0x00100003        disarm
page 0x0010   0x01, 1 byte, to 0x00100002        trigger enable
page 0x0020   the term block for slot 0
page 0x0040   the term block for slot 1
page 0x0010   0x00, 1 byte, to 0x0010000D        tail
page 0x0010   threshold, 2 bytes, to 0x00100008
page 0x0010   position A, 2 bytes, to 0x00100004
page 0x0010   position B, 2 bytes, to 0x00100006
page 0x0010   arm 0x01, 1 byte, to 0x00100003
```

### The masks

Both masks are 5 bytes and cover 34 channels. The mask packs bit `k` for channel
`k`, least significant first, so channel D0 is the lowest bit of the first byte.

Send `FF FF FF FF 3F` to monitor every channel. A zero mask B means no channel is
monitored and the capture never completes.

### The term block for a plain capture

Fourteen frames per slot, all zero, for the seven ungated registers:

```
0x18, 0x19, 0x0E, 0x0A, 0x14, 0x16, 0x17
```

Widths follow the ladder: `0x18` and `0x19` are one byte, `0x0E` and `0x0A` are
four bytes, `0x14`, `0x16` and `0x17` are one byte.

### The term block for an edge trigger

Thirteen frames on slot 0, replacing the plain block:

```
0x12 <- 0        1 byte
0x1A <- 0        4 bytes
0x1F <- 0        4 bytes
0x24 <- 0        4 bytes
0x29 <- 01 00 00 00 00     5 bytes, edge plane A, channel 0
0x2E <- 00 00 00 00 00     5 bytes, edge plane B
0x18 <- 0        1 byte
0x19 <- 0        1 byte
0x0E <- 0        4 bytes
0x0A <- 01 00 00 00        4 bytes, occurrence count of 1
0x14 <- 0        1 byte
0x16 <- 3        1 byte, term class, edge
0x17 <- 1        1 byte, term type
```

Slot 1 carries the plain seven frame block, all zero.

Write the set bit in plane A for one edge polarity and in plane B for the other.
`MEASURED`: with the term in plane A, the capture completes.

### The status byte

Read one byte from register `0x00100000`. The vendor examines bits 0, 6 and 5, in
that order.

| Condition | State |
| --- | --- |
| bit 0 set | capture complete |
| bit 0 clear, bit 6 clear | pre-filling |
| bit 6 set, bit 5 clear | pre-fill complete, waiting for trigger |
| bit 6 set, bit 5 set | post-filling |

Bit 1 is the immediate trigger flag. When set, the host ignores the trigger
position and forces the offset to zero.

**Do not use the status byte as a liveness signal.** It is reliable for the phase
transitions above and unreliable as a completion indicator across configurations.
The measured completion path is in section 8.

### The threshold

The threshold decides the level a channel must cross to read as high.

```
v1   = 1.315 + (volts - 1.315) * (-0.4020381098624974)
code = round((4.559 - v1) / 5.875 * 1023.0)
wire = (code + trim) AND 0x3FF
```

`trim` comes from the device EEPROM and reads zero on a normal unit. At 2.5 volts
the wire word is `0x0288`. At 2.0 volts it is `0x0265`.

**Without a threshold inside the signal swing, the comparator never produces an
edge and the trigger never fires.** A threshold of `0x0288` works for a zero to
five volt input. `MEASURED`.

---

## 6. Timing mode, the internal clock

Timing mode samples from an internal divider. The rate is one row of a 20 row
table, and a client selects the row by sending its two bytes.

| Row | Period | Frequency | Byte A | Byte B |
| --- | --- | --- | --- | --- |
| 0 | 1 ns | 1 GHz | `0x00` | `0x00` |
| 1 | 2 ns | 500 MHz | `0x00` | `0x00` |
| 2 | 4 ns | 250 MHz | `0x00` | `0x00` |
| 3 | 5 ns | 200 MHz | `0x01` | `0x00` |
| 4 | 10 ns | 100 MHz | `0x11` | `0x00` |
| 5 | 20 ns | 50 MHz | `0x11` | `0x11` |
| 6 | 50 ns | 20 MHz | `0x11` | `0x44` |
| 7 | 100 ns | 10 MHz | `0x21` | `0x00` |
| 8 | 200 ns | 5 MHz | `0x21` | `0x11` |
| 9 | 500 ns | 2 MHz | `0x21` | `0x44` |
| 10 | 1 us | 1 MHz | `0x31` | `0x00` |
| 11 | 2 us | 500 kHz | `0x31` | `0x11` |
| 12 | 5 us | 200 kHz | `0x31` | `0x44` |
| 13 | 10 us | 100 kHz | `0x41` | `0x00` |
| 14 | 20 us | 50 kHz | `0x41` | `0x11` |
| 15 | 50 us | 20 kHz | `0x41` | `0x44` |
| 16 | 100 us | 10 kHz | `0x51` | `0x00` |
| 17 | 200 us | 5 kHz | `0x51` | `0x11` |
| 18 | 500 us | 2 kHz | `0x51` | `0x44` |
| 19 | 1 ms | 1 kHz | `0x61` | `0x00` |

Byte A holds the decade in its high nibble and a coarse step in its low bits.
Byte B holds the fine step and takes `0x00`, `0x11` or `0x44`. **Use a pair from
this table.** A pair the table never uses leaves the device pre-filling for ever.
`MEASURED`.

Row 18 checked against a known input: 500 microsecond samples produced 2001
Hz from a 100 Hz signal. Three rows gave three durations tracking pre-fill count
divided by rate. `MEASURED`.

**The two bytes are all a client sends.** The vendor application never sends them,
because its code path for them sits behind a flag that is never set. The hardware
honours them regardless, so a client must send them. `DECODED and MEASURED`.

---

## 7. State mode, the external clock

In state mode the device samples once per edge of an external clock. The sample
rate is the clock rate. `MEASURED`: a 14 kHz clock produced 13650 Hz sampling
against a known input.

### Selecting state mode

Two things select it, and both apply.

**The image.** Sample mode 4 loads images 1 to 4, one per clock source and edge
combination. `DECODED and MEASURED`.

**The mode byte.** Bits 3 and 4 of the mode byte form a two bit field.
**A value of `00` blocks sampling. The values `01`, `10` and `11` all work.** This
build hardwires `11`. `MEASURED`.

| `clkState` | Mode byte |
| --- | --- |
| 0 | `0x1C` |
| 1 | `0x1D` |
| 2 | `0x1A` |
| 3 | `0x1B` |

Compose the mode byte as:

```
mode = clkState + ((~clkState & 2) * 2) + ((otherState ^ 1) * 8) + flag
```

where `clkState` combines the clock source and edge, `otherState` is the qualifier
polarity and `flag` is a boolean. In this build all three carry constant values.

### Which clock each image uses

| Image | Sampling clock |
| --- | --- |
| 1 | CLK1 |
| 3 | CLK1 |
| 2 | CLK2 |
| 4 | CLK2 |

Each image samples on its own input. An image whose own input carries no signal
does not sample. `MEASURED` by crossover.

### Clock source and edge

`clkState` encodes two choices:

| `clkState` | Source | Edge |
| --- | --- | --- |
| 0 | CLK1 | one edge |
| 1 | CLK1 | the other edge |
| 2 | CLK2 | one edge |
| 3 | CLK2 | the other edge |

### The sample position

The dialog offers sampling 2.5 ns before the edge, on the edge, or 2.5 ns after.
**This is not a register.** It selects the image family through a three valued
control: value 0 gives image family 4, value 1 gives image 5, value 2 gives
image 0. A 2.5 ns quantum implies a delay tap inside the image. `DECODED`.

### The qualifier

The dialog offers a qualifier on the clock input that is not the sample clock.
**It never reaches the device.** A closed census found its two settings keys, two
host globals and two settings-struct fields, and no path to any register or
frame. Treat it as a host side setting with no effect on hardware. `DECODED`.

### The base time scale

The dialog shows a reference frequency, 10 MHz by default. **It affects displayed
time intervals only.** The vendor help states this and no code path sends it. A
client may ignore it and compute timestamps from the sampling rate. `DECODED`.

---

## 8. Capturing

### The sample sequence

Three phases. The help file states them.

1. **Pre-fill.** The buffer fills to the pre-trigger level set by position A.
2. **Trigger.** The device keeps sampling and filling while it waits for the
   trigger conditions. The buffer may wrap many times.
3. **Post-fill.** After the trigger the device fills the remaining space, wraps if
   needed, and stops when only the pre-trigger data remains.

### Arming

Write `0x01` to register `0x00100003`.

**Arm byte 1 captures once and does not free-run.** It arms, captures, completes
and goes idle. **Arm byte 0 halts.** The device waits and never completes.
`MEASURED`.

A Recurring capture is therefore a host side repeat. Re-arm after each completion.

### Waiting for completion

Poll register `0x00100000` on a 100 ms tick and read the phase bits from section 5.

A triggered capture moves through the phases on its own when the trigger term
matches. A capture that reaches pre-fill complete and waits for ever has a trigger
that never fired, which almost always means the threshold sits outside the signal
swing.

### Completing a capture on demand

Write `0x01` to register `0x0010000A` and the device exits pre-fill and completes
the capture. This needs no trigger term and no threshold. `MEASURED`.

| Register | Effect | Verified |
| --- | --- | --- |
| `0x0010000A` | exit pre-fill, complete the capture | yes |
| `0x0010000B` | exit post-fill, per decode | no |
| `0x0010000C` | force the trigger, per decode | no |

A client that wants deterministic timing rather than a trigger event can arm and
then issue the phase exit.

### The pre and post fill time limits

The dialog offers a millisecond limit for pre-fill and for post-fill. **The host
enforces them.** The application compares elapsed time against the limit and, on
expiry, writes the constant `0x01` to the phase exit register. The limit value
never reaches the device. `DECODED`.

---

## 9. Reading samples

### The cursor

Read two bytes from register `0x02000000` for cursor A. The cursor is the write
pointer into the ring, masked to 11 bits.

Read the planes at `plane address + cursor`. Each plane window is 4096 bytes and
holds two mirrored copies of the 2048 byte ring, so a linear read of 2048 bytes
from the cursor stays inside the window.

### The planes

| Plane | Channels |
| --- | --- |
| `0x02001000` | D0 to D7, bit 0 is D0 |
| `0x02002000` | D8 to D15 |
| `0x02003000` | D16 to D23 |
| `0x02004000` | D24 to D31 |
| `0x02009000` | flags, and the two clock inputs |

**Channel `k` sits at bit `k mod 8` of its plane, least significant first.**
`MEASURED` three times: D0 at bit 0, D1 at bit 1, D16 at bit 0 of plane 2 and D17
at bit 1.

### Polarity

**A stored bit is the complement of the level.** A set bit means the channel read
low. `MEASURED`: with no signal attached a data plane reads `0xFF`, which is every
channel low.

### The flags plane

| Bit | Meaning |
| --- | --- |
| 0 | pre and post fill boundary. Clear during pre-fill, set during post-fill |
| 1 | CLK1, inverted |
| 2 | CLK2, inverted |
| 3 | record type. Clear for a raw channel-state record |
| 5 and 6 | the second sample clock pair in byte mode. `UNKNOWN` |
| 4 and 7 | zero throughout, unassigned |

**Bit 0 gives the trigger position from the data itself.** Ignore the first and
last record of the plane, then the single transition in bit 0 is the trigger
index. `MEASURED`.

### The sample record format

Five planes, one byte per plane per record. Each record carries one sample.

A record whose flags bit 3 is clear is a **channel-state record**: four data
planes give channels 0 to 31, and flags bits 1 and 2 give channels 32 and 33.

A record whose flags bit 3 is set is a **time delta record**. The value is 35 bits
and carries an interval in sample clock ticks:

```
delta = ((flags & 0x07) << 32) | (plane4 << 24) | (plane3 << 16) | (plane2 << 8) | plane1
```

A decoder walks the records in step, emits a sample for each channel-state record
and advances the running time for each delta record.

**The shipped device stores raw records.** All eight images produce 2047
channel-state records and zero delta records. `MEASURED`.

### The compression setting

The dialog has an Enable Compression checkbox. **It has no route to the device.**
A census found five references to its settings field, all defaults, file load,
file save and interface, and none near any register write. A client reads raw
records and holds the delta decoder ready for a device that produces them.
`DECODED`.

---

## 10. The frequency counter

The device has one counter and the interface shows four slots. A client selects a
channel and reads the count.

| Address | Meaning |
| --- | --- |
| `0x40000000` | channel select, one byte |
| `0x40000001` | count, four bytes, little endian |
| `0x40000005` | status |

Channel numbers are the channel table index:

| Value | Channel |
| --- | --- |
| 0 to 31 | D0 to D31 |
| 32 | CLK1 |
| 33 | CLK2 |

Frequency is the count times ten.

### Procedure

```
1  send a page select for 0x4000
2  write the channel number to 0x40000000
3  wait past the 75 ms gate. 300 ms is comfortable
4  read four bytes from 0x40000001, little endian, and multiply by ten
5  discard that reading and read again after a second gate period
```

**Step 5 is essential.** After a channel change the count register still holds the
previous channel for one gate period. The first read after a change is stale, and
every reading before this was found was a stale one. `MEASURED`.

### Validation

| Channel | Counter | Independent measurement |
| --- | --- | --- |
| D0 | 100 Hz | 102.1 Hz from the captured waveform |
| CLK1 | 14000 Hz | the generator's setting |
| CLK2 | 14000 Hz | the generator's setting |

Reading selects in sequence does not leak between channels. `MEASURED`.

The page select for this bank is `0x4000`. A page select of `0x8000` returns zero
for every read.

---

## 11. Input connector

Forty numbered pins. The wires are D0 to D31, CLK1 and CLK2, and one ground closes
each group of eight.

**Identify a pin by wire and contact colour, not by number.** Pin numbering varies
between units and cost three wrong answers in one session.

| Contact colour | Pins | Wires |
| --- | --- | --- |
| white | 1 to 8 | D0 to D7 |
| blue | 11 to 18 | D8 to D15, and CLK1 at pin 20 |
| yellow | 21 to 28 | D16 to D23 |
| green | 31 to 38 | D24 to D31, and CLK2 at pin 40 |
| black | 9, 19, 29, 39 | ground |

Wire colours inside a group run black, brown, red, orange, yellow, green, blue,
violet. **The two clock inputs are the white wires.**

`LogicPort-Pinout.md` holds the full table.

---

## 12. Unknown

This section lists what no client can rely on yet. Each entry states what is
missing and what would close it.

### The compression enable path

The checkbox has no route to the device, and the two fill limits it enables are
host timed. The only remaining possibility is that compression couples into device
configuration through the mode table builder, which no lane walked. **A client
should assume it cannot enable compression.** Closing it needs either that
decode or traffic from the vendor application, which cannot run on Apple Silicon
because its driver is a 2005 x86 binary.

### Flags bits 5 and 6

They carry the second sample clock pair in byte mode. No measurement exists,
because byte mode itself was never observed.

### Byte mode, ten planes

The decode describes a second read mode with ten planes and two samples per
record, selected when the sample mode value is 1 and the clock table entry has a
divisor of 2. **No bench run has entered it.** A client that needs it must
implement the decode and test it.

### The qualifier

Host side only, per a closed census. Whether the device implements qualification
at all is unknown, since no wire route exists to ask.

### The sample position tap

The 2.5 ns before and after choice selects the image family. The tap inside the
image was not measured, so the actual delay is `UNKNOWN`.

### The rate byte pair semantics

The two clock bytes select a table row and the row gives a period. How a row
becomes a divider inside the device is not decoded.

### The mode table contents

The table at `DAT_0059678c` is built at run time and was never enumerated. It is
the only place a compression coupling could still exist.

### An image index of 7 versus 6

The decode says sample mode 1 loads image 6, or image 7 when a further flag is
set. Both trigger. Which the vendor selects by default is `UNKNOWN`, and a client
may use either.

---

## 13. Quick reference

### A minimal working capture, timing mode

```
clear bitbang, set baud 3000000 and latency 4 ms
load image 7, confirming DONE and INIT
flush, then eight one byte reads
page 0x2000, clock bytes 0x51 and 0x44        row 18, 2 kHz
page 0x0100, mode 0x0C, 0x01000002 <- 0, masks FF FF FF FF 3F twice, aux 0
page 0x0010, 0x00100003 <- 0, 0x00100002 <- 1
page 0x0020, the thirteen frame edge block, edge plane A channel 0
page 0x0040, the seven frame plain block
page 0x0010, 0x0010000D <- 0, threshold 0x0288, positions 1032 and 1016
page 0x0010, arm 0x01
poll status until bit 0 sets
read cursor A, then 2048 bytes from each of the five planes at cursor
```

### Address pages

| Base | Page | Contents |
| --- | --- | --- |
| `0x00100000` | `0x0010` | commands, status, threshold, positions, arm |
| `0x01000000` | `0x0100` | mode byte, auxiliary, masks |
| `0x00200000` | `0x0020` | trigger term slot 0 |
| `0x00400000` | `0x0040` | trigger term slot 1 |
| `0x02000000` | `0x0200` | cursors, sample planes |
| `0x20000000` | `0x2000` | clock bytes |
| `0x40000000` | `0x4000` | frequency counter |

### The frames a client sends, by count

| Stage | Frames |
| --- | --- |
| page select | 4 to 7, one per page used |
| clock | 2 |
| mode block | 6 |
| disarm and enable | 2 |
| term block, edge trigger | 20 |
| tail, threshold, positions | 4 |
| arm | 1 |
| phase exit, optional | 1 |
