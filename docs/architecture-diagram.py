#!/usr/bin/env python3
"""Generate the 1100x700 AWS Marketplace architecture diagram (PNG).

Marketplace requires a 1100x700 px architecture diagram for the "AMI with
CloudFormation" delivery template. This renders one from the logical mermaid
source in docs/architecture.md using AWS category colors. It is a submittable
draft; a designer can swap the colored blocks for the official AWS service
icons (https://aws.amazon.com/architecture/icons) without changing the layout.

Run:  python3 docs/architecture-diagram.py   ->  docs/architecture-diagram.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1100, 700
BG = (247, 248, 250)
INK = (35, 47, 62)        # AWS "squid ink" navy
MUTED = (90, 100, 115)
LINE = (120, 130, 145)

# AWS service category colors
ORANGE = (237, 113, 0)    # Compute (Lambda, EC2)
GREEN = (122, 161, 22)    # Storage (S3)
PINK = (231, 21, 123)     # App integration / management (Step Functions, CloudWatch)
PURPLE = (201, 37, 209)   # IoT / Greengrass
RED = (221, 52, 76)       # Security / IAM
NAVY = (35, 47, 62)


def _font(bold, size):
    base = "/usr/share/fonts/truetype/dejavu/"
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(base + name, size)
    except OSError:
        return ImageFont.load_default()


F_TITLE = _font(True, 24)
F_SUB = _font(False, 13)
F_PANEL = _font(True, 15)
F_NODE = _font(True, 14)
F_NODE_SUB = _font(False, 10)
F_EDGE = _font(False, 10)
F_LEGEND = _font(False, 11)


def text_center(d, cx, cy, s, font, fill):
    x0, y0, x1, y1 = d.textbbox((0, 0), s, font=font)
    d.text((cx - (x1 - x0) / 2, cy - (y1 - y0) / 2), s, font=font, fill=fill)


def text_left(d, x, cy, s, font, fill):
    x0, y0, x1, y1 = d.textbbox((0, 0), s, font=font)
    d.text((x, cy - (y1 - y0) / 2), s, font=font, fill=fill)


def node(d, cx, top, color, title, subtitle, w=320, h=58):
    x0, x1 = cx - w / 2, cx + w / 2
    d.rounded_rectangle([x0, top, x1, top + h], radius=10, fill=color)
    # left accent stripe (darker) for an icon-ish anchor
    d.rounded_rectangle([x0, top, x0 + 10, top + h], radius=10, fill=color)
    text_center(d, cx, top + 20, title, F_NODE, (255, 255, 255))
    text_center(d, cx, top + 40, subtitle, F_NODE_SUB, (255, 255, 255))
    return (cx, top, top + h)  # (center x, top y, bottom y)


def arrow(d, x1, y1, x2, y2, color=LINE, width=3, label=None, dashed=False):
    if dashed:
        _dash(d, x1, y1, x2, y2, color, width)
    else:
        d.line([x1, y1, x2, y2], fill=color, width=width)
    # arrowhead
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 9
    p1 = (x2 - size * math.cos(ang - 0.5), y2 - size * math.sin(ang - 0.5))
    p2 = (x2 - size * math.cos(ang + 0.5), y2 - size * math.sin(ang + 0.5))
    d.polygon([(x2, y2), p1, p2], fill=color)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        pad = 3
        bb = d.textbbox((0, 0), label, font=F_EDGE)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.rectangle([mx - tw / 2 - pad, my - th / 2 - pad, mx + tw / 2 + pad, my + th / 2 + pad],
                    fill=BG)
        text_center(d, mx, my, label, F_EDGE, MUTED)


def _dash(d, x1, y1, x2, y2, color, width, dash=9, gap=6):
    import math
    total = math.hypot(x2 - x1, y2 - y1)
    dx, dy = (x2 - x1) / total, (y2 - y1) / total
    pos = 0
    while pos < total:
        a = pos
        b = min(pos + dash, total)
        d.line([x1 + dx * a, y1 + dy * a, x1 + dx * b, y1 + dy * b], fill=color, width=width)
        pos += dash + gap


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Title
    text_center(d, W / 2, 26, "DEEPX Compiler + Greengrass  —  AWS Marketplace (AMI with CloudFormation)", F_TITLE, INK)
    text_center(d, W / 2, 52, "One subscription, one CloudFormation stack: cloud compile (ONNX -> DXNN) + edge deploy (Greengrass V2 -> DEEPX NPU)", F_SUB, MUTED)

    # Buyer
    bx0, by0, bx1, by1 = 320, 74, 780, 116
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=12, fill=NAVY)
    text_center(d, (bx0 + bx1) / 2, (by0 + by1) / 2, "Buyer  ·  subscribe -> launch ONE CloudFormation stack", F_NODE, (255, 255, 255))

    # Panels
    d.rounded_rectangle([35, 140, 535, 665], radius=14, outline=(205, 212, 222), width=2)
    d.rounded_rectangle([565, 140, 1065, 665], radius=14, outline=(205, 212, 222), width=2)
    text_left(d, 55, 158, "COMPILE PIPELINE (cloud)", F_PANEL, INK)
    text_left(d, 585, 158, "EDGE DEPLOY — Greengrass V2", F_PANEL, INK)

    cxL, cxR = 285, 815
    top = 178
    step = 78

    # Compile column
    s3 = node(d, cxL, top, GREEN, "S3  ModelBucket", "*.onnx + *.json in  ·  *.dxnn out")
    lam = node(d, cxL, top + step, ORANGE, "Lambda  TriggerFunction", "pairs onnx + json, starts workflow")
    sfn = node(d, cxL, top + 2 * step, PINK, "Step Functions  CompilerStateMachine", "runInstances -> sendCommand -> terminate")
    ec2 = node(d, cxL, top + 3 * step, ORANGE, "EC2  DEEPX Compiler AMI (ephemeral)", "dxcom via SSM CompilerDocument")
    cw = node(d, cxL, top + 4 * step, PINK, "CloudWatch Logs", "execution + state-machine logs")
    for a, b in [(s3, lam), (lam, sfn), (sfn, ec2), (ec2, cw)]:
        arrow(d, cxL, a[2], cxL, b[1] - 2)
    # .dxnn back to S3 (loop on the right side of the column)
    arrow(d, cxL + 168, ec2[1] + 20, cxL + 168, s3[2] - 8, color=GREEN, width=2, label=".dxnn")
    d.line([cxL + 168, ec2[1] + 20, cxL + 160, ec2[1] + 20], fill=GREEN, width=2)
    d.line([cxL + 168, s3[2] - 8, cxL + 160, s3[2] - 8], fill=GREEN, width=2)

    # Edge column
    pub = node(d, cxR, top, ORANGE, "Lambda  ComponentPublish", "custom resource: publishes component")
    dep = node(d, cxR, top + step, PURPLE, "GreengrassV2  Deployment", "com.deepx.dx-runtime + aws.greengrass.Cli")
    tg = node(d, cxR, top + 2 * step, PURPLE, "IoT  Thing Group", "deployment target for core devices")
    dev = node(d, cxR, top + 3 * step, NAVY, "Edge core devices  (DEEPX NPU)", "install driver / fw / dx_rt / dx_stream")
    art = node(d, cxR, top + 4 * step, GREEN, "Public S3  dx-runtime artifacts", "driver.deb · dx_rt · fw.bin · dx_stream")
    ter = node(d, cxR, top + 5 * step, RED, "IAM  Token exchange role", "logs-scoped, assumed by core devices")
    for a, b in [(pub, dep), (dep, tg)]:
        arrow(d, cxR, a[2], cxR, b[1] - 2)
    arrow(d, cxR, tg[2], cxR, dev[1] - 2, label="deploys")
    arrow(d, cxR, dev[2], cxR, art[1] - 2, label="download + build")
    arrow(d, cxR - 168, dev[2] + 8, cxR - 168, ter[1] + 20, color=RED, width=2, label="assume")
    d.line([cxR - 168, dev[2] + 8, cxR - 160, dev[2] + 8], fill=RED, width=2)
    d.line([cxR - 168, ter[1] + 20, cxR - 160, ter[1] + 20], fill=RED, width=2)

    # Buyer -> panels
    arrow(d, 450, by1, cxL, s3[1] - 2)
    arrow(d, 650, by1, cxR, pub[1] - 2)

    # Cross: compiled .dxnn delivered to edge device
    arrow(d, s3[0] + 160, s3[1] + 28, dev[0] - 160, dev[1] + 28,
          color=(150, 120, 60), width=2, label="compiled .dxnn delivered to edge", dashed=True)

    # Legend
    ly = 678
    items = [("Compute", ORANGE), ("Storage", GREEN), ("App integ. / Mgmt", PINK),
             ("IoT / Greengrass", PURPLE), ("Security (IAM)", RED)]
    lx = 55
    for name, color in items:
        d.rounded_rectangle([lx, ly, lx + 16, ly + 14], radius=3, fill=color)
        text_left(d, lx + 22, ly + 7, name, F_LEGEND, MUTED)
        lx += 22 + d.textbbox((0, 0), name, font=F_LEGEND)[2] + 28

    out = Path(__file__).with_name("architecture-diagram.png")
    img.save(out, "PNG")
    print(f"wrote {out}  ({W}x{H})")


if __name__ == "__main__":
    main()
