#!/usr/bin/env python3
"""LogicPort frame exchanges and handshakes, as SVG (labelled layout)."""
esc = lambda s: s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
XH, XD = 470, 1210
W = 1500
s=[]
def emit(y, frm, to, side_label, payload, colour="#33475b", marker="a", dashed=False):
    out=[]
    dash = ' stroke-dasharray="6,4"' if dashed else ''
    out.append(f'<line x1="{frm}" y1="{y}" x2="{to}" y2="{y}" stroke="{colour}" stroke-width="1.6"{dash} marker-end="url(#{marker})"/>')
    if side_label:
        out.append(f'<text x="34" y="{y+4}" font-size="11.5" font-weight="bold" fill="{colour}">{esc(side_label)}</text>')
    if payload:
        mid=(frm+to)//2
        tw=len(payload)*6.4+16
        out.append(f'<rect x="{mid-tw/2}" y="{y-26}" width="{tw}" height="20" fill="#ffffff" stroke="#e0e0e0"/>')
        out.append(f'<text x="{mid}" y="{y-12}" font-size="11" font-family="Menlo,monospace" text-anchor="middle" fill="#1a1a1a">{esc(payload)}</text>')
    return out, y+56
blocks=[
 ("1: SEGMENT SELECT (C3)", "sent once per bank change, then cached", "#1f4e79", [
   (XH,XD,"HOST → DEV","C3 10 00 00 00   segment 0x0010 = command bank", "#33475b","a",False),
   (XD,XH,"DEVICE → HOST","C3 00 01   exactly 3 bytes, echo validated", "#b45309","r",False),
 ]),
 ("2: REGISTER WRITE (C1)", "frame then payload, then a 3-byte ack", "#1f4e79", [
   (XH,XD,"HOST → DEV","C1 03 00 00 00 00   arm register, 1-byte payload", "#33475b","a",False),
   (XD,XH,"DEVICE → HOST","C1 00 02   exactly 3 bytes", "#b45309","r",False),
 ]),
 ("3: REGISTER READ (C2)", "request N, receive N + 4", "#1f4e79", [
   (XH,XD,"HOST → DEV","C2 00 00 01 00   request 1 payload byte", "#33475b","a",False),
   (XD,XH,"DEVICE → HOST","C2 00 03 00 42   4-byte header, then payload", "#b45309","r",False),
 ]),
 ("4: INVALID FRAME", "the command layer rejects and says so", "#8b2c2c", [
   (XH,XD,"HOST → DEV","99 00 00 00 00 61   bad opcode", "#8b2c2c","a",False),
   (XD,XH,"DEVICE → HOST","EE 00 00   structured error reply", "#b45309","r",False),
 ]),
]
# measure
H = 190 + sum(46 + len(b[3])*56 + 26 for b in blocks) + 190
s.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica,Arial,sans-serif">')
s.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
s.append('<defs><marker id="a" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#33475b"/></marker>'
         '<marker id="r" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#b45309"/></marker></defs>')
s.append('<text x="34" y="42" font-size="19" font-weight="bold" fill="#1a1a1a">LogicPort frame exchanges: host to device, decoded 2026-10-29</text>')
s.append('<text x="34" y="66" font-size="11.5" fill="#555555">Every accepted command returns an echo. One counter increments per command in both directions, so a gap detects a lost or duplicated frame.</text>')
s.append(f'<rect x="{XH-100}" y="92" width="200" height="34" rx="3" fill="#dae8fc" stroke="#6c8ebf"/>')
s.append(f'<text x="{XH}" y="115" font-size="13" font-weight="bold" text-anchor="middle" fill="#12385e">HOST (libftdi)</text>')
s.append(f'<rect x="{XD-100}" y="92" width="200" height="34" rx="3" fill="#d5e8d4" stroke="#82b366"/>')
s.append(f'<text x="{XD}" y="115" font-size="13" font-weight="bold" text-anchor="middle" fill="#1d4d1d">DEVICE (FPGA)</text>')
s.append(f'<line x1="{XH}" y1="126" x2="{XH}" y2="{H-170}" stroke="#9bb7d4" stroke-dasharray="5,4"/>')
s.append(f'<line x1="{XD}" y1="126" x2="{XD}" y2="{H-170}" stroke="#9bc49b" stroke-dasharray="5,4"/>')
y=180
for title,sub,col,rows in blocks:
    s.append(f'<text x="34" y="{y}" font-size="13.5" font-weight="bold" fill="{col}">{esc(title)}</text>')
    s.append(f'<text x="34" y="{y+18}" font-size="11" fill="#666666">{esc(sub)}</text>')
    y+=46
    for frm,to,lab,payload,c,m,d in rows:
        out,y = emit(y,frm,to,lab,payload,c,m,d); s.extend(out)
    y+=10
# rules
yb=H-170
s.append(f'<rect x="34" y="{yb}" width="{W-68}" height="146" rx="4" fill="#f7f7f7" stroke="#cccccc"/>')
s.append(f'<text x="50" y="{yb+24}" font-size="13" font-weight="bold" fill="#1a1a1a">Rules that bit the bench, all learned on hardware</text>')
for i,r in enumerate([
 "Drain exactly 3 bytes after every C1 and C3. A C2 returns N + 4. A reply left unread desynchronises everything after it.",
 "The segment select is cached host-side and skipped when unchanged, so one C3 covers a run of accesses in the same bank.",
 "A segment select must be followed by a WRITE before any read. That frame order decided two device deaths, each costing a replug.",
 "A C3 with a non-zero segment wedged the device when followed by reads alone; with a write first it survived four segment changes.",
 "Reads must add the sample cursor to the plane offset. Reading planeOffset + 0 returns unwritten buffer and looks like an idle device.",
]):
    s.append(f'<text x="56" y="{yb+48+i*20}" font-size="11" fill="#333333">•  {esc(r)}</text>')
s.append('</svg>')
open("logicport-handshake.svg","w").write("\n".join(s)); print("svg written, height", H)
