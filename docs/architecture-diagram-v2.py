#!/usr/bin/env python3
"""Generate the 1100x700 AWS Marketplace architecture diagram, v2 (PNG).

Dark-navy restyle of architecture-diagram.py, matching the Marketplace hero
image design language (dark gradient, subtle grid, glass cards, teal accents).
Same logical content: compile pipeline (left), edge deploy (right), cross-flow
connectors (.dxnn write-back, OTA artifact delivery, IAM assume).

Renders an HTML layout with headless Chrome at 2x and downscales with Pillow.

Requires: google-chrome, Pillow.
Run:  python3 docs/architecture-diagram-v2.py  ->  docs/architecture-diagram-v2.png
"""
import base64
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

W, H = 1100, 700  # Marketplace-required size for architecture diagrams
OUT = Path(__file__).with_name("architecture-diagram-v2.png")

# Layout constants
LX, LY, LW, LH = 40, 146, 480, 496    # left panel (compile pipeline)
RX, RY, RW, RH = 580, 146, 480, 496   # right panel (edge deploy)
LCX, LCW, LCH, LSTEP = 74, 412, 66, 94
RCX, RCW, RCH, RSTEP = 614, 412, 58, 78
LYS = [172 + i * LSTEP for i in range(5)]
RYS = [168 + i * RSTEP for i in range(6)]

# Muted AWS service category colors (accent only), as icon gradients
COLORS = {
    "orange": ("#F5921E", "#D96A00"),  # Compute (Lambda, EC2)
    "green": ("#7AB93A", "#4E8A1E"),   # Storage (S3)
    "blue": ("#4A8DE8", "#2563C4"),    # App integration / management
    "purple": ("#A05CE0", "#7534BF"),  # IoT / Greengrass
    "red": ("#E8536A", "#C22843"),     # Security / IAM
    "teal": ("#17B8A0", "#0B7E70"),    # brand accent (DEEPX NPU)
}

# (icon text, color key, title, description, highlighted)
LEFT_CARDS = [
    ("S3", "green", "S3 &middot; ModelBucket",
     "*.onnx + *.json in &nbsp;/&nbsp; *.dxnn out", False),
    ("&lambda;", "orange", "Lambda &middot; TriggerFunction",
     "pairs onnx + json, starts workflow", False),
    ("SF", "blue", "Step Functions &middot; StateMachine",
     "runInstances, sendCommand, terminate", False),
    ("EC2", "orange", "EC2 &middot; DEEPX Compiler AMI",
     "dxcom via SSM (ephemeral instance)", True),
    ("CW", "blue", "CloudWatch Logs",
     "execution + state-machine logs", False),
]
RIGHT_CARDS = [
    ("&lambda;", "orange", "Lambda &middot; ComponentPublish",
     "custom resource: publishes component", False),
    ("GG", "purple", "Greengrass V2 &middot; Deployment",
     "com.deepx.dx-runtime + greengrass.Cli", False),
    ("IoT", "purple", "IoT &middot; Thing Group",
     "deployment target for core devices", False),
    ("NPU", "teal", "Edge core devices (DEEPX NPU)",
     "driver / firmware / dx_rt / dx_stream", True),
    ("S3", "green", "Public S3 &middot; dx-runtime artifacts",
     "driver.deb / dx_rt / fw.bin / dx_stream", False),
    ("IAM", "red", "IAM &middot; Token exchange role",
     "logs-scoped, assumed by core devices", False),
]

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:1100px; height:700px; overflow:hidden; }
body {
  font-family:'Lato','Noto Sans',sans-serif; position:relative; color:#fff;
  background:
    radial-gradient(ellipse 650px 380px at 85% 12%, rgba(0,196,167,.10), transparent 65%),
    radial-gradient(ellipse 650px 460px at 6% 96%, rgba(37,99,235,.14), transparent 65%),
    linear-gradient(140deg,#0A1224 0%, #0C1B36 55%, #0A2233 100%);
}
.grid { position:absolute; inset:0;
  background-image:linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
  background-size:40px 40px;
  -webkit-mask-image:radial-gradient(ellipse 90% 90% at 50% 45%, #000 30%, transparent 100%); }
.accent-top { position:absolute; top:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg,#2563EB,#00C4A7 55%,transparent); }

h1 { position:absolute; top:14px; width:100%; text-align:center; font-size:24px; font-weight:900; letter-spacing:-.01em; }
h1 .gg { background:linear-gradient(90deg,#34D9C3,#4F8DF9); -webkit-background-clip:text; background-clip:text; color:transparent; }
.sub { position:absolute; top:46px; width:100%; text-align:center; font-size:12px; color:#A7B7CC; }

.buyer { position:absolute; top:70px; left:50%; transform:translateX(-50%);
  padding:8px 22px; border-radius:999px; font-size:13px; font-weight:800; color:#EAF6F3;
  background:linear-gradient(160deg, rgba(0,196,167,.16), rgba(0,196,167,.04));
  border:1px solid rgba(52,217,195,.55);
  box-shadow:0 0 24px rgba(0,196,167,.18), 0 6px 18px rgba(0,0,0,.3); }
.buyer b { color:#3BE3C8; }

.panel { position:absolute; border-radius:16px;
  background:rgba(255,255,255,.030); border:1px solid rgba(255,255,255,.10);
  box-shadow:0 10px 30px rgba(0,0,0,.25); }
.badge { position:absolute; padding:5px 14px; border-radius:999px; font-size:11px;
  font-weight:800; letter-spacing:.10em; text-transform:uppercase; z-index:3; }
.badge.bl { background:linear-gradient(90deg,#2563EB,#3B82F6); box-shadow:0 4px 14px rgba(37,99,235,.4); }
.badge.pu { background:linear-gradient(90deg,#7C3AED,#9333EA); box-shadow:0 4px 14px rgba(124,58,237,.4); }
.badge span { color:rgba(255,255,255,.75); font-weight:600; }

.card { position:absolute; display:flex; align-items:center; gap:12px; padding:0 14px;
  border-radius:11px; background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.13);
  box-shadow:0 6px 18px rgba(0,0,0,.28); z-index:2; }
.card.hot { background:linear-gradient(160deg, rgba(0,196,167,.14), rgba(0,196,167,.04));
  border:1px solid rgba(52,217,195,.55);
  box-shadow:0 0 22px rgba(0,196,167,.16), 0 6px 18px rgba(0,0,0,.3); }
.icon { width:38px; height:38px; border-radius:9px; flex:none;
  display:flex; align-items:center; justify-content:center;
  font-size:12px; font-weight:900; color:#fff;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.35), 0 3px 8px rgba(0,0,0,.35); }
.txt .t { font-size:13.5px; font-weight:800; color:#F2F6FB; }
.txt .d { font-size:10.5px; color:#93A7C0; margin-top:2px;
  font-family:'DejaVu Sans Mono',monospace; letter-spacing:-.02em; }
.card.hot .txt .d { color:#7DD3C8; }

.flowchip { position:absolute; z-index:4; padding:3px 10px; border-radius:999px;
  font-size:10px; font-weight:700; letter-spacing:.02em; white-space:nowrap;
  background:#0E2434; border:1px solid rgba(52,217,195,.45); color:#7DD3C8;
  box-shadow:0 3px 10px rgba(0,0,0,.4); transform:translate(-50%,-50%); }
.flowchip.gray { border-color:rgba(148,171,200,.4); color:#B7C7DA; }
.flowchip.red { border-color:rgba(232,83,106,.55); color:#F0899B; }

.legend { position:absolute; bottom:12px; width:100%; display:flex; justify-content:center; gap:24px; }
.legend .li { display:flex; align-items:center; gap:7px; font-size:11px; color:#A7B7CC; font-weight:600; }
.legend .dot { width:10px; height:10px; border-radius:3px; }
svg.overlay { position:absolute; inset:0; z-index:1; }
"""


def card_html(x, y, w, h, icon, color, title, desc, hot):
    c1, c2 = COLORS[color]
    hotcls = " hot" if hot else ""
    return (
        f'<div class="card{hotcls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;">'
        f'<div class="icon" style="background:linear-gradient(150deg,{c1},{c2});">{icon}</div>'
        f'<div class="txt"><div class="t">{title}</div><div class="d">{desc}</div></div></div>'
    )


def build_html():
    cards = ""
    for i, (ic, co, t, d, hot) in enumerate(LEFT_CARDS):
        cards += card_html(LCX, LYS[i], LCW, LCH, ic, co, t, d, hot)
    for i, (ic, co, t, d, hot) in enumerate(RIGHT_CARDS):
        cards += card_html(RCX, RYS[i], RCW, RCH, ic, co, t, d, hot)

    # in-column flow arrows
    arrows = ""
    lmid, rmid = LCX + LCW // 2, RCX + RCW // 2
    for i in range(len(LEFT_CARDS) - 1):
        arrows += (f'<line x1="{lmid}" y1="{LYS[i]+LCH+3}" x2="{lmid}" y2="{LYS[i+1]-5}" '
                   f'stroke="rgba(148,171,200,.55)" stroke-width="1.6" marker-end="url(#ar)"/>')
    for i in range(len(RIGHT_CARDS) - 1):
        arrows += (f'<line x1="{rmid}" y1="{RYS[i]+RCH+3}" x2="{rmid}" y2="{RYS[i+1]-5}" '
                   f'stroke="rgba(148,171,200,.55)" stroke-width="1.6" marker-end="url(#ar)"/>')

    # cross-flow anchor points
    s3_right = (LCX + LCW, LYS[0] + 24)           # left S3 card, right edge
    s3_back = (LCX + LCW, LYS[0] + LCH - 10)      # .dxnn write-back entry
    ec2_right = (LCX + LCW, LYS[3] + LCH // 2)    # EC2 card, right edge
    dev_left = (RCX, RYS[3] + RCH // 2)           # edge devices, left edge
    dev_assume = (RCX, RYS[3] + RCH - 6)          # assume line exit
    iam_left = (RCX, RYS[5] + RCH // 2)           # IAM card, left edge
    elbow_x = LCX + LCW + 26
    assume_x = RCX - 22

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="grid"></div><div class="accent-top"></div>

<h1>DEEPX <span class="gg">Greengrass</span> Solution</h1>
<div class="sub">One subscription, one CloudFormation stack &mdash; cloud compile (ONNX &rarr; DXNN) plus edge deploy (Greengrass V2 &rarr; DEEPX NPU)</div>
<div class="buyer">Buyer: subscribe &amp; launch <b>ONE</b> CloudFormation stack</div>

<div class="panel" style="left:{LX}px;top:{LY}px;width:{LW}px;height:{LH}px;"></div>
<div class="panel" style="left:{RX}px;top:{RY}px;width:{RW}px;height:{RH}px;"></div>
<div class="badge bl" style="left:{LX+18}px;top:{LY-13}px;">Compile Pipeline <span>(cloud)</span></div>
<div class="badge pu" style="left:{RX+18}px;top:{RY-13}px;">Edge Deploy <span>(Greengrass V2)</span></div>

{cards}

<svg class="overlay" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="rgba(148,171,200,.75)"/></marker>
    <marker id="art" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#34D9C3"/></marker>
    <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#E8536A"/></marker>
  </defs>
  {arrows}
  <!-- buyer -> panels -->
  <line x1="500" y1="106" x2="{LX+260}" y2="{LY-8}" stroke="rgba(148,171,200,.5)" stroke-width="1.6" marker-end="url(#ar)"/>
  <line x1="600" y1="106" x2="{RX+220}" y2="{RY-8}" stroke="rgba(148,171,200,.5)" stroke-width="1.6" marker-end="url(#ar)"/>
  <!-- EC2 -> S3 (.dxnn write-back) -->
  <path d="M{ec2_right[0]+4},{ec2_right[1]} L{elbow_x},{ec2_right[1]} L{elbow_x},{s3_back[1]} L{s3_back[0]+8},{s3_back[1]}"
        fill="none" stroke="#34D9C3" stroke-width="1.8" stroke-dasharray="6 5" marker-end="url(#art)" opacity=".85"/>
  <!-- S3 -> edge devices (compiled .dxnn, OTA) -->
  <path d="M{s3_right[0]+6},{s3_right[1]} C{s3_right[0]+110},{s3_right[1]+10} {dev_left[0]-115},{dev_left[1]-25} {dev_left[0]-6},{dev_left[1]}"
        fill="none" stroke="#34D9C3" stroke-width="1.8" stroke-dasharray="6 5" marker-end="url(#art)" opacity=".85"/>
  <!-- devices -> IAM (assume) -->
  <path d="M{dev_assume[0]-4},{dev_assume[1]} L{assume_x},{dev_assume[1]} L{assume_x},{iam_left[1]} L{iam_left[0]-4},{iam_left[1]}"
        fill="none" stroke="#E8536A" stroke-width="1.7" stroke-dasharray="5 5" marker-end="url(#arr)" opacity=".9"/>
</svg>

<div class="flowchip" style="left:{elbow_x}px;top:{(s3_back[1]+ec2_right[1])//2}px;">.dxnn</div>
<div class="flowchip" style="left:{(s3_right[0]+dev_left[0])//2}px;top:{(s3_right[1]+dev_left[1])//2 - 12}px;">compiled .dxnn &rarr; edge</div>
<div class="flowchip gray" style="left:{rmid+78}px;top:{RYS[3]-10}px;">deploys</div>
<div class="flowchip gray" style="left:{rmid+78}px;top:{RYS[4]-10}px;">download + build (OTA)</div>
<div class="flowchip red" style="left:{assume_x}px;top:{(dev_assume[1]+iam_left[1])//2}px;">assume</div>

<div class="legend">
  <div class="li"><div class="dot" style="background:#F5921E;"></div>Compute</div>
  <div class="li"><div class="dot" style="background:#7AB93A;"></div>Storage</div>
  <div class="li"><div class="dot" style="background:#4A8DE8;"></div>App integ. / Mgmt</div>
  <div class="li"><div class="dot" style="background:#A05CE0;"></div>IoT / Greengrass</div>
  <div class="li"><div class="dot" style="background:#E8536A;"></div>Security (IAM)</div>
</div>
</body></html>"""


def main():
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "arch.html"
        png2x = Path(tmp) / "arch_2x.png"
        html_path.write_text(build_html(), encoding="utf-8")
        subprocess.run(
            ["google-chrome", "--headless", "--disable-gpu",
             "--force-device-scale-factor=2", f"--window-size={W},{H}",
             "--hide-scrollbars", f"--screenshot={png2x}", str(html_path)],
            check=True, capture_output=True)
        img = Image.open(png2x).resize((W, H), Image.LANCZOS).convert("RGB")
        img.save(OUT, optimize=True)
    print(f"wrote {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
