#!/usr/bin/env python3
"""Generate the 1100x700 AWS Marketplace architecture diagram (PNG).

Marketplace requires a 1100x700 px architecture diagram for the "AMI with
CloudFormation" delivery template. This renders a clean, icon-anchored diagram
of the logical architecture using muted AWS category colors. It is a
submittable draft; a designer can swap the accent chips for the official AWS
service icons (https://aws.amazon.com/architecture/icons) without changing the
layout.

Run:  python3 docs/architecture-diagram.py   ->  docs/architecture-diagram.png
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1100, 700
SCALE = 2  # supersample for crisp edges, downscaled on save

BG = (246, 248, 251)
CARD = (255, 255, 255)
INK = (33, 45, 61)
MUTED = (108, 119, 135)
BORDER = (223, 229, 238)
LINE = (150, 160, 175)
PANEL_BG = (251, 252, 254)

# Muted AWS service category colors (accent only)
ORANGE = (234, 138, 52)    # Compute (Lambda, EC2)
GREEN = (122, 161, 22)     # Storage (S3)
BLUE = (63, 122, 199)      # App integration / management
PURPLE = (140, 90, 205)    # IoT / Greengrass
RED = (214, 69, 80)        # Security / IAM
NAVY = (33, 45, 61)
TEAL = (10, 179, 156)      # brand accent / data flow

FDIR = "/usr/share/fonts/truetype/dejavu/"


def _font(bold, size):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(FDIR + name, size * SCALE)
    except OSError:
        return ImageFont.load_default()


F_TITLE = _font(True, 23)
F_SUB = _font(False, 13)
F_PANEL = _font(True, 13)
F_NODE = _font(True, 13)
F_NODE_SUB = _font(False, 10)
F_EDGE = _font(True, 9)
F_LEGEND = _font(False, 11)
F_ICON = _font(True, 15)

S = SCALE


def tc(d, cx, cy, s, font, fill):
    x0, y0, x1, y1 = d.textbbox((0, 0), s, font=font)
    d.text((cx * S - (x1 - x0) / 2, cy * S - (y1 - y0) / 2), s, font=font, fill=fill)


def tl(d, x, cy, s, font, fill):
    x0, y0, x1, y1 = d.textbbox((0, 0), s, font=font)
    d.text((x * S, cy * S - (y1 - y0) / 2), s, font=font, fill=fill)


def card(d, cx, top, color, icon, title, subtitle, w=330, h=62):
    x0, x1 = (cx - w / 2), (cx + w / 2)
    # soft shadow
    d.rounded_rectangle([x0 * S + 2 * S, (top + 3) * S, x1 * S + 2 * S, (top + h + 3) * S],
                        radius=11 * S, fill=(232, 236, 242))
    d.rounded_rectangle([x0 * S, top * S, x1 * S, (top + h) * S],
                        radius=11 * S, fill=CARD, outline=BORDER, width=1 * S)
    # left accent chip with icon glyph
    chip = 40
    ix0, iy0 = (x0 + 12), (top + (h - chip) / 2)
    d.rounded_rectangle([ix0 * S, iy0 * S, (ix0 + chip) * S, (iy0 + chip) * S],
                        radius=8 * S, fill=color)
    tc(d, ix0 + chip / 2, iy0 + chip / 2, icon, F_ICON, (255, 255, 255))
    tx = x0 + 12 + chip + 14
    tl(d, tx, top + 22, title, F_NODE, INK)
    tl(d, tx, top + 42, subtitle, F_NODE_SUB, MUTED)
    return (cx, top, top + h)


def _dash(d, x1, y1, x2, y2, color, width, dash=10, gap=7):
    total = math.hypot(x2 - x1, y2 - y1)
    if total == 0:
        return
    dx, dy = (x2 - x1) / total, (y2 - y1) / total
    pos = 0
    while pos < total:
        a, b = pos, min(pos + dash, total)
        d.line([(x1 + dx * a) * S, (y1 + dy * a) * S, (x1 + dx * b) * S, (y1 + dy * b) * S],
               fill=color, width=width * S)
        pos += dash + gap


def arrow(d, x1, y1, x2, y2, color=LINE, width=2, label=None, dashed=False):
    if dashed:
        _dash(d, x1, y1, x2, y2, color, width)
    else:
        d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=color, width=width * S)
    ang = math.atan2(y2 - y1, x2 - x1)
    sz = 8
    p1 = ((x2 - sz * math.cos(ang - 0.5)) * S, (y2 - sz * math.sin(ang - 0.5)) * S)
    p2 = ((x2 - sz * math.cos(ang + 0.5)) * S, (y2 - sz * math.sin(ang + 0.5)) * S)
    d.polygon([(x2 * S, y2 * S), p1, p2], fill=color)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        bb = d.textbbox((0, 0), label, font=F_EDGE)
        tw, th = (bb[2] - bb[0]) / S, (bb[3] - bb[1]) / S
        pad = 6
        d.rounded_rectangle([(mx - tw / 2 - pad) * S, (my - th / 2 - pad) * S,
                             (mx + tw / 2 + pad) * S, (my + th / 2 + pad) * S],
                            radius=(th / 2 + pad) * S, fill=(238, 242, 247))
        tc(d, mx, my, label, F_EDGE, MUTED)


def panel(d, x0, y0, x1, y1, label, color):
    d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=16 * S,
                        fill=PANEL_BG, outline=BORDER, width=1 * S)
    # header pill
    bb = d.textbbox((0, 0), label, font=F_PANEL)
    tw = (bb[2] - bb[0]) / S
    px0, py0 = x0 + 20, y0 - 12
    d.rounded_rectangle([px0 * S, py0 * S, (px0 + tw + 36) * S, (py0 + 26) * S],
                        radius=13 * S, fill=color)
    tl(d, px0 + 18, py0 + 13, label, F_PANEL, (255, 255, 255))


def main():
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    tc(d, W / 2, 30, "DEEPX Greengrass Solution", F_TITLE, INK)
    tc(d, W / 2, 56,
       "One subscription, one CloudFormation stack: cloud compile (ONNX to DXNN) plus edge deploy (Greengrass V2 to DEEPX NPU)",
       F_SUB, MUTED)

    # Buyer
    bx0, by0, bx1, by1 = 328, 82, 772, 124
    d.rounded_rectangle([bx0 * S, by0 * S, bx1 * S, by1 * S], radius=13 * S, fill=NAVY)
    tc(d, (bx0 + bx1) / 2, (by0 + by1) / 2, "Buyer: subscribe and launch ONE CloudFormation stack", F_NODE, (255, 255, 255))

    # Panels
    panel(d, 40, 165, 540, 665, "COMPILE PIPELINE  (cloud)", BLUE)
    panel(d, 560, 165, 1060, 665, "EDGE DEPLOY  (Greengrass V2)", PURPLE)

    cxL, cxR = 290, 810
    top = 200
    step = 82

    # Compile column
    s3 = card(d, cxL, top, GREEN, "S3", "S3  ModelBucket", "*.onnx + *.json in / *.dxnn out")
    lam = card(d, cxL, top + step, ORANGE, "λ", "Lambda  TriggerFunction", "pairs onnx + json, starts workflow")
    sfn = card(d, cxL, top + 2 * step, BLUE, "SF", "Step Functions  StateMachine", "runInstances, sendCommand, terminate")
    ec2 = card(d, cxL, top + 3 * step, ORANGE, "EC2", "EC2  DEEPX Compiler AMI", "dxcom via SSM (ephemeral instance)")
    cw = card(d, cxL, top + 4 * step, BLUE, "CW", "CloudWatch Logs", "execution + state-machine logs")
    for a, b in [(s3, lam), (lam, sfn), (sfn, ec2), (ec2, cw)]:
        arrow(d, cxL, a[2], cxL, b[1] - 2)
    # .dxnn loop back to S3
    rx = cxL + 178
    d.line([rx * S, (ec2[1] + 20) * S, (cxL + 150) * S, (ec2[1] + 20) * S], fill=TEAL, width=2 * S)
    _dash(d, rx, ec2[1] + 20, rx, s3[2] - 6, TEAL, 2)
    arrow(d, rx, s3[2] - 6, cxL + 150, s3[2] - 6, color=TEAL, width=2, label=".dxnn")

    # Edge column
    pub = card(d, cxR, top, ORANGE, "λ", "Lambda  ComponentPublish", "custom resource: publishes component")
    dep = card(d, cxR, top + step, PURPLE, "GG", "GreengrassV2  Deployment", "com.deepx.dx-runtime + greengrass.Cli")
    tg = card(d, cxR, top + 2 * step, PURPLE, "IoT", "IoT  Thing Group", "deployment target for core devices")
    dev = card(d, cxR, top + 3 * step, NAVY, "NPU", "Edge core devices  (DEEPX NPU)", "driver / firmware / dx_rt / dx_stream")
    art = card(d, cxR, top + 4 * step, GREEN, "S3", "Public S3  dx-runtime artifacts", "driver.deb / dx_rt / fw.bin / dx_stream")
    ter = card(d, cxR, top + 5 * step, RED, "IAM", "IAM  Token exchange role", "logs-scoped, assumed by core devices")
    for a, b in [(pub, dep), (dep, tg)]:
        arrow(d, cxR, a[2], cxR, b[1] - 2)
    arrow(d, cxR, tg[2], cxR, dev[1] - 2, label="deploys")
    arrow(d, cxR, dev[2], cxR, art[1] - 2, label="download + build (OTA)")
    lx = cxR - 178
    d.line([lx * S, (dev[2] + 6) * S, (cxR - 150) * S, (dev[2] + 6) * S], fill=RED, width=2 * S)
    _dash(d, lx, dev[2] + 6, lx, ter[1] + 20, RED, 2)
    arrow(d, lx, ter[1] + 20, cxR - 150, ter[1] + 20, color=RED, width=2, label="assume")

    # Buyer -> panels
    arrow(d, 470, by1, cxL, s3[1] - 2)
    arrow(d, 630, by1, cxR, pub[1] - 2)

    # Cross: compiled .dxnn delivered to edge
    arrow(d, s3[0] + 170, s3[1] + 31, dev[0] - 170, dev[1] + 31,
          color=TEAL, width=2, label="compiled .dxnn to edge", dashed=True)

    # Legend
    ly = 678
    items = [("Compute", ORANGE), ("Storage", GREEN), ("App integ. / Mgmt", BLUE),
             ("IoT / Greengrass", PURPLE), ("Security (IAM)", RED)]
    lx = 55
    for name, color in items:
        d.rounded_rectangle([lx * S, ly * S, (lx + 14) * S, (ly + 14) * S], radius=4 * S, fill=color)
        tl(d, lx + 20, ly + 7, name, F_LEGEND, MUTED)
        lx += 20 + d.textbbox((0, 0), name, font=F_LEGEND)[2] / S + 26

    out = Path(__file__).with_name("architecture-diagram.png")
    img.resize((W, H), Image.LANCZOS).save(out, "PNG")
    print(f"wrote {out}  ({W}x{H})")


if __name__ == "__main__":
    main()
