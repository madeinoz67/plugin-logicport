#!/usr/bin/env python3
"""LogicPort client API layers, as SVG."""
esc = lambda s: s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
layers = [
 ("6 · CLIENT API", "#e8eef7", "#4a6f9c",
  ["connect / disconnect", "configure(sample mode, rate, threshold, masks)", "arm(trigger mode, terms)", "poll() -> phase", "fetch() -> per-channel samples"],
  "Must not assume a capture completed because the call returned; the phase byte decides."),
 ("5 · ACQUISITION MODEL", "#e6f0e6", "#4d804d",
  ["three phases: pre-fill, trigger, post-fill", "status byte at segment 0x0010 offset 0", "cursor read at 0x0200:0x0000, trigger word at 0x0010:0x000A", "plane reads at planeOffset + cursor, count = 0x800 - start"],
  "Must not read planes without the cursor, and must not treat status 0x42 as 'not ready': data is fetchable at that status."),
 ("4 · REGISTER / SEMANTIC LAYER", "#fdf1e0", "#a97b32",
  ["arm 0x100003, threshold 0x100008, positions 0x100004/6", "mode byte 0x1000000, masks 0x1000003/0x1000008", "edge planes 0x200029/0x20002E, pattern 0x200000/0x200005", "count 0x20000A, derived tail 0x200014-0x200019"],
  "Must not treat the mode/mask bank planes as trigger conditions; the trigger planes live in the term banks."),
 ("3 · ADDRESSING LAYER", "#f0e8f5", "#7a5a96",
  ["segment = address >> 16, sent as a C3 frame", "segment cached host-side, skipped when unchanged", "low 16 bits of the address ride the C1/C2 frame", "prime each segment with a write before any read"],
  "Must not read immediately after a segment select, and must not assume the segment is still selected after an error."),
 ("2 · FRAME LAYER", "#e6eef0", "#3f7080",
  ["C1 write, C2 read, C3 segment select", "header [op][sel_lo][sel_hi][len_lo][len_hi], len = payload_len - 1", "C1 and C3 drain exactly 3 bytes; a C2 returns its length field plus four", "validate the echo; EE 00 00 means the frame was rejected"],
  "Must not leave a reply unread, and must not reuse a stale reply for the next command."),
 ("1 · TRANSPORT", "#f2f2f2", "#666666",
  ["libftdi bulk transfers; VID 0x0403 PID 0xDC48", "latency timer 4, baud 3000000", "FPGA bitstream load after every replug (.ccf transform)", "flush: purge, 65535 x 00, 30000 x 00, one 0x01, purge"],
  "Must not assume the device survived a bad frame; the LED going dark means a replug, and no command recovers it."),
]
W=1560
rowh=118; top=110
H = top + len(layers)*(rowh+14) + 40
s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica,Arial,sans-serif">']
s.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
s.append('<text x="30" y="42" font-size="19" font-weight="bold" fill="#1a1a1a">LogicPort client API layers: what each layer owns</text>')
s.append('<text x="30" y="66" font-size="11.5" fill="#555555">Read top down for the call path, bottom up for the evidence. Each layer states what it must not assume, taken from a failure observed on the bench.</text>')
y=top
for name, fill, stroke, owns, caution in layers:
    s.append(f'<rect x="30" y="{y}" width="{W-60}" height="{rowh}" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    s.append(f'<text x="46" y="{y+26}" font-size="13.5" font-weight="bold" fill="{stroke}">{esc(name)}</text>')
    for i,o in enumerate(owns):
        col = i % 2; row = i // 2
        s.append(f'<text x="{46+col*740}" y="{y+50+row*18}" font-size="11" fill="#222222">• {esc(o)}</text>')
    s.append(f'<text x="46" y="{y+rowh-10}" font-size="10.5" font-style="italic" fill="#8b2c2c">Caution: {esc(caution)}</text>')
    y += rowh + 14
s.append('</svg>')
open("logicport-layers.svg","w").write("\n".join(s)); print("svg written, height", H)
