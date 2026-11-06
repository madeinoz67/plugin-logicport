# LogicPort client guide

> How to drive the Intronix LogicPort LA1034 from your own code. This guide walks
> from an empty file to a decoded capture, and then through the trigger, state
> mode and the frequency counter. Each step carries the bytes it sends.
>
> The full technical detail is in `LogicPort-Technical-Reference.md`. Read that
> when you need the register map or the reasoning. This file is the walkthrough.

---

## 1. What you need

| Item | Detail |
| --- | --- |
| Device | LogicPort LA1034, USB vendor `0x0403`, product `0xDC48` |
| Bitstream | `LogicPort.CCF`, shipped with the vendor installer |
| Transport | any library that can open the FTDI device directly |
| Python example | `pyftdi`, which wraps libftdi |

The device is not a serial port. Open it through an FTDI library and not through
a virtual COM port.

The examples below use `pyftdi`. The frame bytes are the same in any language.

---

## 2. The five ideas

Everything else follows from these.

1. **A frame is a command.** Three kinds: write, read, and page select.
2. **An address has two halves.** The low 16 bits travel in the frame. The high
   bits travel in a page select.
3. **The device needs a bitstream.** Load it after every replug.
4. **A capture has three phases.** The buffer pre-fills, waits for a trigger, then
   post-fills.
5. **A stored bit is the complement of the level.** A set bit means the channel read low.

---

## 3. Build the frames

Start here, with no hardware. This part is testable on its own.

```python
def write(reg: int, payload: bytes) -> bytes:
    """A C1 frame. The length field is the payload length minus one."""
    return bytes([0xC1, reg & 0xFF, (reg >> 8) & 0xFF,
                  (len(payload) - 1) & 0xFF, ((len(payload) - 1) >> 8) & 0xFF]) + payload

def read(reg: int, count: int) -> bytes:
    """A C2 frame. The length field is the byte count minus one."""
    return bytes([0xC2, reg & 0xFF, (reg >> 8) & 0xFF,
                  (count - 1) & 0xFF, ((count - 1) >> 8) & 0xFF])

def select(page: int) -> bytes:
    """A C3 frame. The field is the address page."""
    return bytes([0xC3, page & 0xFF, (page >> 8) & 0xFF, 0x00, 0x00])
```

Check that they look right before you touch a device.

```python
assert write(0x00100003, b"\x01") == bytes.fromhex("C10100000001")
assert read(0x02000000, 2)        == bytes.fromhex("C200000100")
assert select(0x0010)             == bytes.fromhex("C310000000")
```

### Reading a reply

A write and a page select each reply with three bytes. A read replies with three
overhead bytes and then the payload you asked for.

```python
def payload_of(reply: bytes) -> bytes:
    """Three overhead bytes, then the payload."""
    return reply[3:]
```

A one byte read therefore returns four bytes and the value sits at index 3.

---

## 4. Talk to the device

A thin binding around whichever transport you have. This one uses `pyftdi`.

```python
from pyftdi.ftdi import Ftdi

class Link:
    def __init__(self):
        self.f = Ftdi()
        self.f.open(vendor=0x0403, product=0xDC48)
        self.f.set_baudrate(3_000_000)
        self.f.set_latency_timer(4)
        self.page = None

    def send(self, frame: bytes) -> bytes:
        self.f.write_data(frame)
        want = 3 if frame[0] in (0xC1, 0xC3) else ((frame[3] | frame[4] << 8) + 4)
        return self.f.read_data(want)

    def go(self, page: int):
        """Select a page, retrying. A silent failure sends later reads to the wrong page."""
        if self.page == page:
            return
        for _ in range(3):
            reply = self.send(select(page))
            if len(reply) == 3:
                self.page = page
                return
        raise RuntimeError(f"page {page:#06x} did not answer")
```

**Three Mbaud, not 460800.** The slower rate turns a one megabyte bitstream load
into a 23 second wait.

---

## 5. Load the bitstream

The device holds nothing after a replug, so this runs first, every time.

```python
def image_bytes(ccf_path: str, index: int) -> bytes:
    """Extract one image from the CCF, with the two page exchange applied."""
    raw = open(ccf_path, "rb").read()
    newline = raw.index(b"\n")
    lengths = [int(x) ^ 0x916662 for x in raw[:newline].decode().split(",")[:8]]

    body = raw[newline + 1:]
    offset = sum(n * 16 for n in lengths[:index])
    image = bytearray(body[offset:offset + lengths[index] * 16])

    # exchange the first two 4096 byte pages. Without this DONE never asserts.
    image[0:4096], image[4096:8192] = image[4096:8192], image[0:4096]
    return bytes(image)
```

Then stream it while the device is in bitbang mode.

```python
def load(link: Link, image: bytes):
    f = link.f
    f.set_bitmode(0x00, Ftdi.BitMode.RESET)
    f.set_bitmode(0x07, Ftdi.BitMode.BITBANG)

    f.write_data(b"\x00")                       # begin configuration
    if f.read_pins() & 0x10:
        raise RuntimeError("DONE still high. The device holds an image, so it "
                           "ignores this stream. Unconfigure first.")
    f.write_data(b"\x04")                       # begin the stream
    for i in range(0, len(image), 10000):
        f.write_data(image[i:i + 10000])
    if not f.read_pins() & 0x10:
        raise RuntimeError("DONE did not assert. Check the page exchange.")

    f.set_bitmode(0x00, Ftdi.BitMode.RESET)
```

### Confirm which image is loaded

```python
def loaded_image(link: Link) -> int:
    link.go(0x0010)
    return payload_of(link.send(read(0x0010000F, 2)))[0] & 0x0F
```

**Read this back after every load, and before every capture.** A load that never
took leaves the previous index in place, so a capture can look healthy while
running the wrong image. There are eight images, indexed 0 to 7.

| Index | Load it when | Edge trigger |
| --- | --- | --- |
| 7 | you want a triggered capture on the internal clock | **yes** |
| 6 | the same as 7. Both belong to sample mode 1 | **yes** |
| 1, 3 | you want to sample on an edge of an external clock on CLK1 | no |
| 2, 4 | you want to sample on an edge of an external clock on CLK2 | no |
| 0 | sample mode 2, a timing mode with no established distinct purpose | no |
| 5 | sample mode 3, a timing mode with no established distinct purpose | no |

Use image 7 for everything in this guide, because it is the image whose trigger
works.

Two things to know before you vary the image.

**Load one image, never all eight.** The file holds eight alternatives and not
eight parts of one image. A single stream of the whole body faulted the device.

**To change image, unconfigure first.** A configured device keeps DONE high, so a
new stream is a no-op. Write `0x00` to register `0x00100000`, confirm DONE drops,
then run the load.


---

## 6. Your first capture

Timing mode samples from the internal clock. This sequence produces a capture with
no trigger term, by exiting the pre-fill phase.

```python
def configure(link: Link):
    link.go(0x2000)                              # clock bytes: row 18, 2 kHz
    link.send(write(0x0000, b"\x51"))
    link.send(write(0x0001, b"\x44"))

    link.go(0x0100)                              # mode block
    link.send(write(0x0000, b"\x0C"))            # mode byte, timing mode
    link.send(write(0x0002, b"\x00"))
    link.send(write(0x0003, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x3F])))   # mask A
    link.send(write(0x0008, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x3F])))   # mask B
    link.send(write(0x0001, b"\x00"))            # auxiliary, always zero

    link.go(0x0010)                              # disarm, then enable
    link.send(write(0x0003, b"\x00"))
    link.send(write(0x0002, b"\x01"))

    for page in (0x0020, 0x0040):                # seven ungated frames per slot
        link.go(page)
        for reg, width in ((0x18, 1), (0x19, 1), (0x0E, 4), (0x0A, 4),
                           (0x14, 1), (0x16, 1), (0x17, 1)):
            link.send(write(reg, bytes(width)))

    link.go(0x0010)                              # tail, threshold, positions
    link.send(write(0x000D, b"\x00"))
    link.send(write(0x0008, b"\x88\x02"))        # threshold 2.5 V
    link.send(write(0x0004, b"\x08\x04"))        # position A, 1032 samples
    link.send(write(0x0006, b"\xF8\x03"))        # position B, 1016 samples
    link.send(write(0x0003, b"\x01"))            # arm
```

Then complete the capture and read it.

```python
def capture(link: Link) -> list[bytes]:
    time.sleep(0.5)                              # let pre-fill run
    link.go(0x0010)
    link.send(write(0x000A, b"\x01"))            # exit pre-fill, complete
    time.sleep(0.3)

    link.go(0x0200)
    cursor = int.from_bytes(payload_of(link.send(read(0x02000000, 2)))[:2], "little")
    cursor &= 0x7FF

    planes = []
    for base in (0x1000, 0x2000, 0x3000, 0x4000, 0x9000):
        planes.append(payload_of(link.send(read(base + cursor, 2048))))
    return planes
```

---

## 7. Turn bytes into channels

Each plane carries eight channels. A set bit means the channel read low.

```python
def channel(planes, index: int) -> list[int]:
    """The logical level of one channel, one entry per sample."""
    plane = planes[index // 8]
    bit = index % 8
    return [0 if (byte >> bit) & 1 else 1 for byte in plane]
```

So `channel(planes, 0)` is D0 and `channel(planes, 16)` is D16.

### Read a frequency from the samples

```python
def transitions(values: list[int]) -> int:
    return sum(1 for a, b in zip(values, values[1:]) if a != b)
```

At 2 kHz a 100 Hz signal gives about 30 transitions in a 2048 sample buffer, and a
500 Hz signal gives about 150. **A transition count means nothing without the
sample rate beside it.**

### Find the trigger position

Flags plane bit 0 is clear during pre-fill and set during post-fill.

```python
def trigger_index(planes) -> int:
    flags = planes[4]
    for i in range(1, len(flags) - 1):
        if (flags[i] & 1) != (flags[i - 1] & 1):
            return i
    return -1
```

Ignore the first and last record, which are boundary sentinels.

---

## 8. Use the trigger

An edge trigger fires when a channel crosses the threshold in a chosen direction.
This is the term block that works, and it replaces the plain block for slot 0.

```python
def edge_term(link: Link, ch: int, rising: bool = True):
    plane_a = 1 << ch if rising else 0        # 40 bit value, channel k at bit k
    plane_b = 0 if rising else 1 << ch
    link.go(0x0020)
    link.send(write(0x0012, b"\x00"))
    for reg in (0x1A, 0x1F, 0x24):
        link.send(write(reg, bytes(4)))
    link.send(write(0x0029, plane_a.to_bytes(5, "little")))
    link.send(write(0x002E, plane_b.to_bytes(5, "little")))
    link.send(write(0x0018, b"\x00"))
    link.send(write(0x0019, b"\x00"))
    link.send(write(0x000E, bytes(4)))
    link.send(write(0x000A, (1).to_bytes(4, "little")))   # occurrence count of 1
    link.send(write(0x0014, b"\x00"))
    link.send(write(0x0016, b"\x03"))          # term class, edge
    link.send(write(0x0017, b"\x01"))          # term type
```

Then leave slot 1 with the plain seven frame block from section 6, and arm as
before. The capture completes on its own when the edge arrives.

**The trigger only fires if the threshold sits inside the signal swing.** A
threshold of `0x0288` suits a zero to five volt input. If the capture reaches
pre-fill complete and waits for ever, the threshold is the first thing to check.

### If the trigger never fires

Complete the capture anyway by writing `0x01` to register `0x0010000A`. This needs
no term and no threshold, and it is how a client gets deterministic timing.

---

## 9. Use state mode

State mode samples once per edge of an external clock, so the sample rate is the
clock rate.

```python
STATE = {0: 0x1C, 1: 0x1D, 2: 0x1A, 3: 0x1B}   # clkState to mode byte

def configure_state(link: Link, clk_state: int):
    link.go(0x0100)
    link.send(write(0x0000, bytes([STATE[clk_state]])))
    # the rest of the mode block, then the term block, then the tail, as before
```

Three rules decide whether state mode works.

1. **Use a state image.** Images 1 and 3 sample on CLK1. Images 2 and 4 sample on
   CLK2. An image whose own input carries no signal does not sample.
2. **The mode byte field at bits 3 and 4 must not be zero.** The values in the
   table above all work.
3. **The sample rate is the clock rate.** A 14 kHz clock gives 13650 Hz sampling.

The 10 MHz base time scale in the dialog affects only the time intervals the vendor
interface displays. Compute your own timestamps from the clock rate.

The qualifier checkbox never reaches the device, so a single clock on the right
input is enough.

---

## 10. Read the frequency counter

The device counts edges and reports in units of ten hertz.

```python
def frequency(link: Link, ch: int) -> int:
    """Channel 0 to 31 for D0 to D31, 32 for CLK1, 33 for CLK2."""
    link.go(0x4000)
    link.send(write(0x0000, bytes([ch])))
    time.sleep(0.3)
    link.send(read(0x0001, 4))                    # first read: stale, discard
    time.sleep(0.2)
    count = int.from_bytes(payload_of(link.send(read(0x0001, 4))), "little")
    return count * 10
```

**The two reads are both needed.** After a channel change the count register holds
the previous channel for one gate period, so the first read is stale.

Measured agreement: D0 read 100 Hz where the captured waveform gave 102.1 Hz, and
both clock inputs read 14000 Hz against a 14 kHz generator.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Every read returns `0xFF` | a page select failed silently and the cache is stale | retry the page select until it answers |
| DONE never asserts during a load | the two page exchange is missing | exchange the first two 4096 byte pages |
| The load is ignored entirely | the device already holds an image, so DONE stays high | unconfigure with `0x00` to `0x00100000` first |
| The LED goes dark | a fault state, and the measured cause is writing `0x61` to `0x00100000` | replug. Never send that byte |
| A capture waits for ever at pre-fill complete | the threshold sits outside the signal swing, or the trigger never matches | set the threshold to the middle of the swing, or exit pre-fill with `0x0010000A` |
| The counter returns another channel's value | the first read after a channel change is stale | discard one read |
| State mode samples at roughly 2 kHz whatever the clock does | the image has no signal on its own clock input | check which input the image uses, and drive it |
| Plane data looks like noise | nothing is attached, and floating inputs read as noise | confirm the channel by moving the signal |

---

## 12. Where to read further

| Topic | File |
| --- | --- |
| Register map, record format, every rule | `LogicPort-Technical-Reference.md` |
| Pinout and channel map | `LogicPort-Pinout.md` |
| The research record and its retractions | `LogicPort-Protocol.md` |
| Trigger reference in depth | `LogicPort-Trigger-Programming.md` |
| The vendor project file format | `LogicPort-LPF-Format.md` |
