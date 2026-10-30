#!/usr/bin/env python3
"""Generate the LogicPort register/address map as SVG + drawio from one model."""
banks = [
 ("Command bank", "segment 0x0010", [
   ("0x100000","status / phase byte",1),("0x100001","auxiliary",0),
   ("0x100002","trigger enable / disarm",0),("0x100003","ARM",1),
   ("0x100004","trigger position A",1),("0x100005","no known function",0),
   ("0x100006","trigger position B",0),("0x100008","threshold DAC",1),
   ("0x100009","no known function",0),("0x10000A","phase-exit slot 1",0),
   ("0x10000B","phase-exit slot 2",0),("0x10000C","phase-exit slot 3",0),
   ("0x10000D","no known function",0),("0x10000F","no known function",0)]),
 ("Mode / mask bank", "segment 0x0100", [
   ("0x1000000","clock / mode byte",1),("0x1000001","auxiliary",0),
   ("0x1000002","trigger enable",0),("0x1000003","sample mask A",0),
   ("0x1000008","sample mask B",0)]),
 ("Term slot 0", "segment 0x0020", [
   ("0x200000","pattern plane A",0),("0x200005","pattern plane B",0),
   ("0x20000A","occurrence count",1),("0x20000E","derived dword",0),
   ("0x200012","digit mode",0),("0x200014","derived flag",0),
   ("0x200016","count-mode flag",0),("0x200017","count-mode flag",0),
   ("0x200018","count-mode flag",0),("0x200019","enable byte",0),
   ("0x20001A","digit sum",0),("0x20001F","string A value",0),
   ("0x200024","string B value",0),("0x200029","EDGE plane A",1),
   ("0x20002E","EDGE plane B",1)]),
 ("Term slot 1", "segment 0x0040", [
   ("0x400000","pattern plane A",0),("0x400005","pattern plane B",0),
   ("0x40000A","occurrence count",1),("0x40000E","derived dword",0),
   ("0x400012","digit mode",0),("0x400014","derived flag",0),
   ("0x400016","count-mode flag",0),("0x400017","count-mode flag",0),
   ("0x400018","count-mode flag",0),("0x400019","enable byte",0),
   ("0x40001A","digit sum",0),("0x40001F","string A value",0),
   ("0x400024","string B value",0),("0x400029","EDGE plane A",1),
   ("0x40002E","EDGE plane B",1)]),
 ("Sample area", "segment 0x0200", [
   ("0x…0000","cursor A (2 bytes BE)",1),("0x…0100","cursor B (2 bytes BE)",0),
   ("0x…1000","D0–D7   set A",1),("0x…2000","D8–D15  set A",0),
   ("0x…3000","D16–D23 set A",0),("0x…4000","D24–D31 set A",0),
   ("0x…5000","D0–D7   set B",0),("0x…6000","D8–D15  set B",0),
   ("0x…7000","D16–D23 set B",0),("0x…8000","D24–D31 set B",0),
   ("0x…9000","D32/CLK1 + D33/CLK2",0)]),
 ("Clock bytes", "segment 0x2000", [
   ("0x20000000","clock byteA",1),("0x20000001","clock byteB",0)]),
 ("Freq counter", "segment 0x4000", [
   ("0x40000000","channel select / marker",1),("0x40000001","count dword",1),
   ("0x40000005","status",0)]),
]
TITLE = "LogicPort register and address map: decoded from DefineHardwareAddresses, 2026-10-29"
NOTES = [
 ("Addressing", "A register is reached by a C3 frame carrying the segment = address >> 16, then a C1/C2 frame whose 16-bit field is the low half of the address."),
 ("Caching", "The segment is cached (one C3 covers a run in the same bank). A segment select must be followed by a write before any read."),
 ("Widths", "1, 2, 4 and 40-bit payloads. The four plane registers are 40-bit: bit k = channel k, 34 channels, top 6 bits zero."),
 ("Encodings", "EDGE is four-state per channel (0 none, 1 plane A, 2 plane B, 3 both). PATTERN is ternary (0 don't care, 1 must-be-0, 2 must-be-1) and never uses 3."),
]
COLW, ROWH, HDRH, GAP = 268, 24, 44, 22
X0, Y0 = 30, 92
maxrows = max(len(b[2]) for b in banks)
W = X0*2 + len(banks)*(COLW+GAP) - GAP
H = Y0 + HDRH + 10 + maxrows*(ROWH+1) + 30 + len(NOTES)*20 + 30
esc = lambda s: s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica,Arial,sans-serif">']
svg.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
svg.append(f'<text x="{X0}" y="44" font-size="19" font-weight="bold" fill="#1a1a1a">{esc(TITLE)}</text>')
for bi,(name,seg,regs) in enumerate(banks):
    x = X0 + bi*(COLW+GAP); y = Y0
    svg.append(f'<rect x="{x}" y="{y}" width="{COLW}" height="{HDRH}" rx="2" fill="#dae8fc" stroke="#6c8ebf"/>')
    svg.append(f'<text x="{x+8}" y="{y+18}" font-size="12.5" font-weight="bold" fill="#12385e">{esc(name)}</text>')
    svg.append(f'<text x="{x+8}" y="{y+35}" font-size="10.5" fill="#37587a">{esc(seg)}</text>')
    y += HDRH + 10
    for addr,desc,hot in regs:
        fill, stroke, fw = ("#ffe6cc","#d79b00","bold") if hot else ("#ffffff","#cccccc","normal")
        svg.append(f'<rect x="{x}" y="{y}" width="{COLW}" height="{ROWH}" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>')
        svg.append(f'<text x="{x+7}" y="{y+16}" font-size="10" font-weight="{fw}" fill="#1a1a1a">{esc(addr)}</text>')
        svg.append(f'<text x="{x+118}" y="{y+16}" font-size="10" fill="#333333">{esc(desc)}</text>')
        y += ROWH + 1
yb = Y0 + HDRH + 10 + maxrows*(ROWH+1) + 26
for i,(k,v) in enumerate(NOTES):
    svg.append(f'<text x="{X0}" y="{yb+i*20}" font-size="11" fill="#333333"><tspan font-weight="bold">{esc(k)}.</tspan> {esc(v)}</text>')
svg.append('</svg>')
open("logicport-register-map.svg","w").write("\n".join(svg))
print("svg written")
