#!/usr/bin/env python3
"""LogicPort bitstream loading, image selection and validation, as SVG.

Five panels: the file, the load sequence with pin states, the selection rule,
the readback that proves which image is resident, and the measured behaviour.
"""
esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

W = 1560
s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="1080" '
     f'viewBox="0 0 {W} 1080" font-family="Helvetica,Arial,sans-serif">']
s.append(f'<rect width="{W}" height="1080" fill="#ffffff"/>')
s.append('<text x="30" y="40" font-size="19" font-weight="bold" fill="#1a1a1a">'
         'Bitstream: what loads, how it loads, and how to prove it</text>')
s.append('<text x="30" y="62" font-size="11.5" fill="#555555">'
         'The device does nothing until a bitstream holds. The file carries eight separate images and a client loads exactly one.</text>')


def panel(x, y, w, h, title, fill, stroke):
    s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="1.4"/>')
    s.append(f'<text x="{x+16}" y="{y+24}" font-size="13.5" font-weight="bold" '
             f'fill="{stroke}">{esc(title)}</text>')


def line(x, y, text, size=11, colour="#222222", weight="normal", mono=False):
    family = ' font-family="Menlo,monospace"' if mono else ""
    s.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{colour}" '
             f'font-weight="{weight}"{family}>{esc(text)}</text>')


# ---- panel 1: the file -----------------------------------------------------
panel(30, 86, 740, 250, "1 · The file: LogicPort.CCF", "#eef3fa", "#4a6f9c")
line(46, 112, "First line is ASCII: nine decimals, comma separated.", 11)
line(46, 130, "XOR each with 0x916662 to recover its value.", 11)
line(46, 156, "values 0 to 7   image lengths, in units of 16 bytes", 11, mono=True)
line(46, 174, "value 8         XOR checksum of the eight, advisory", 11, mono=True)
line(46, 200, "Body starts after the newline and the eight images tile it exactly.", 11)
line(46, 218, "Shipped body is 8387696 bytes.", 11)
line(46, 244, "Exchange the first 4096 bytes with the next 4096, before streaming.", 11, "#8b2c2c", "bold")
line(46, 262, "Without that exchange the device never asserts DONE.", 11, "#8b2c2c")
line(46, 288, "Stream the bytes unchanged. The body uses only 0x04 to 0x07.", 11)
line(46, 306, "Do not decode or expand them.", 11)
for i, (length, x) in enumerate([(1057840, 46), (1044512, 132), (1046896, 218),
                                 (1047328, 304), (1046512, 390), (1054992, 476),
                                 (1031232, 562), (1058384, 648)]):
    s.append(f'<rect x="{x}" y="{318}" width="80" height="12" fill="#9bb4d4" stroke="#4a6f9c"/>')

# ---- panel 2: the load sequence --------------------------------------------
panel(790, 86, 740, 250, "2 · The load sequence, with the pins it checks",
      "#e9f3e9", "#4d804d")
steps = [
    ("open the device, baud 3000000, latency 4 ms", ""),
    ("enter bitbang mode, mask 0x07", ""),
    ("write 0x00", "DONE must drop to low"),
    ("write 0x04", "INIT must go high within 1000 ms"),
    ("stream the image in 10000 byte chunks", ""),
    ("wait for DONE", "DONE must go high within 1000 ms"),
    ("leave bitbang mode, then flush and prime", ""),
]
y = 122
for i, (step, check) in enumerate(steps, 1):
    line(806, y, f"{i}. {step}", 11)
    if check:
        line(1090, y, check, 10.5, "#4d804d", "italic")
    y += 20
line(806, y + 8, "If DONE stays high at step 3, the device already holds an image", 11, "#8b2c2c")
line(806, y + 26, "and the stream is a no-op. Unconfigure first: write 0x00 to", 11, "#8b2c2c")
line(806, y + 44, "register 0x00100000, confirm DONE drops, then load.", 11, "#8b2c2c")
line(806, y + 70, "Never write 0x61 to that register. It faults the device.", 11, "#8b2c2c", "bold")

# ---- panel 3: selection ----------------------------------------------------
panel(30, 352, 740, 190, "3 · Which image to load", "#fdf4e8", "#a97b32")
line(46, 380, "Sample mode 1 is the mode whose edge trigger works.", 11, "#8b2c2c", "bold")
rows = [("sample mode", "clkState", "image"), ("2", "not applicable", "0"),
        ("3", "not applicable", "5"), ("1", "not applicable", "6, or 7"),
        ("4", "0, 1, 2, 3", "1, 3, 2, 4")]
y = 404
for i, (a, b, c) in enumerate(rows):
    weight = "bold" if i == 0 else "normal"
    line(46, y, a.ljust(16), 11, "#333333", weight, mono=True)
    line(210, y, b.ljust(18), 11, "#333333", weight, mono=True)
    line(400, y, c, 11, "#333333", weight, mono=True)
    y += 20
line(46, y + 12, "Images 1 and 3 sample on CLK1. Images 2 and 4 sample on CLK2.", 11)
line(46, y + 30, "The sample position setting selects the image family, not a register.", 11)

# ---- panel 4: validation ---------------------------------------------------
panel(790, 352, 740, 190, "4 · Proving which image is resident", "#f4ecf7", "#7a5a96")
line(806, 380, "Read two bytes from register 0x0010000F.", 11)
line(806, 400, "The low byte holds the image index with bit 4 set.", 11)
line(806, 420, "Index 7 reads 0x17.  Index 0 reads 0x10.", 11, mono=True)
line(806, 446, "This is the only reliable way to know what the device holds.", 11, "#8b2c2c", "bold")
line(806, 466, "A load that never took leaves the previous index in place, so", 11)
line(806, 484, "read this before trusting any capture that follows.", 11)
line(806, 510, "The register is read only. Nothing writes it.", 11)

# ---- panel 5: behaviour ----------------------------------------------------
panel(30, 558, 1500, 210, "5 · What each image does, measured", "#f2f2f2", "#666666")
cols = ["image", "answers", "pre-fill", "edge trigger"]
xs = [46, 200, 340, 480]
for x, c in zip(xs, cols):
    line(x, 586, c, 11, "#333333", "bold", mono=True)
behaviour = [("0", "yes", "yes", "no"), ("1", "yes", "yes", "no"),
             ("2", "yes", "yes", "no"), ("3", "yes", "yes", "no"),
             ("4", "yes", "yes", "no"), ("5", "yes", "yes", "no"),
             ("6", "yes", "yes", "TRIGGERS"), ("7", "yes", "yes", "TRIGGERS")]
y = 606
for a, b, c, d in behaviour:
    line(46, y, a, 11, "#222222", mono=True)
    line(200, y, b, 11, "#222222", mono=True)
    line(340, y, c, 11, "#222222", mono=True)
    colour = "#8b2c2c" if d == "TRIGGERS" else "#222222"
    line(480, y, d, 11, colour, "bold" if d == "TRIGGERS" else "normal", mono=True)
    y += 20
line(700, 606, "Every image answers commands and fills the buffer.", 11)
line(700, 626, "Only images 6 and 7 fire the edge trigger.", 11, "#8b2c2c", "bold")
line(700, 646, "Images 6 and 7 both belong to sample mode 1, so a client", 11)
line(700, 664, "that needs a triggered capture selects that mode.", 11)
line(700, 692, "Measured with a working client, one image per run,", 10.5, "#666666", "italic")
line(700, 708, "and the resident index read back each time.", 10.5, "#666666", "italic")

# ---- panel 6: the fault rule ----------------------------------------------
panel(30, 786, 1500, 120, "6 · Two rules that cost the bench the most time",
      "#fdf0f0", "#8b2c2c")
line(46, 814, "The LED reports health, not configuration.", 11.5, "#8b2c2c", "bold")
line(46, 834, "A healthy device with no bitstream shows the LED lit. A dark LED means a write faulted it, and only a replug clears it.",
     11)
line(46, 860, "Never write 0x61 to register 0x00100000.", 11.5, "#8b2c2c", "bold")
line(46, 880, "That one write is the measured cause of the fault state. A client has no reason to send it.", 11)

s.append('</svg>')
open("logicport-bitstream-images.svg", "w").write("\n".join(s))
print("svg written")
