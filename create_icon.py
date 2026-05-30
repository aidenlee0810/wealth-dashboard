#!/usr/bin/env python3
"""
Wealth Dashboard — macOS 아이콘 생성 스크립트
출력: icon.png (1024×1024)
"""
from PIL import Image, ImageDraw
import math, os

SIZE   = 1024
CX, CY = SIZE // 2, SIZE // 2

# 색상
BG_TOP    = (11, 20, 42)
BG_BOT    = (7,  13, 28)
GREEN     = (16, 185, 129)
GREEN_DIM = (10, 120,  85)
WHITE     = (255, 255, 255)
GOLD      = (251, 191,  36)

# ─── 1. 기본 캔버스 (그라데이션 배경) ───────────────────────────
img  = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# 배경 그라데이션 (위→아래)
bg = Image.new('RGBA', (SIZE, SIZE))
bg_d = ImageDraw.Draw(bg)
for y in range(SIZE):
    t   = y / SIZE
    r   = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
    g   = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
    b   = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
    bg_d.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

# ─── 2. macOS 스쿼클 마스크 ────────────────────────────────────
mask = Image.new('L', (SIZE, SIZE), 0)
md   = ImageDraw.Draw(mask)
RADIUS = int(SIZE * 0.225)
md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)

# 배경을 마스크로 합성
img.paste(bg, (0, 0), mask)

draw = ImageDraw.Draw(img)

# ─── 3. 미묘한 그리드 선 ───────────────────────────────────────
GRID_COLOR = (255, 255, 255, 10)
for i in range(1, 4):
    y_pos = int(SIZE * 0.28 + i * SIZE * 0.15)
    draw.line([(SIZE * 0.12, y_pos), (SIZE * 0.88, y_pos)],
              fill=GRID_COLOR, width=1)

# ─── 4. 상승 트렌드 라인 (점선 효과) ─────────────────────────
trend_pts = [
    (int(SIZE * 0.14), int(SIZE * 0.68)),
    (int(SIZE * 0.30), int(SIZE * 0.60)),
    (int(SIZE * 0.50), int(SIZE * 0.42)),
    (int(SIZE * 0.68), int(SIZE * 0.36)),
    (int(SIZE * 0.86), int(SIZE * 0.22)),
]
for i in range(len(trend_pts) - 1):
    x0, y0 = trend_pts[i]
    x1, y1 = trend_pts[i + 1]
    draw.line([(x0, y0), (x1, y1)],
              fill=(16, 185, 129, 90), width=4)

# ─── 5. 막대 차트 (3개 바) ────────────────────────────────────
bars = [
    {'h': 0.26, 'x': 0.18},
    {'h': 0.44, 'x': 0.37},
    {'h': 0.60, 'x': 0.56},
]
BAR_W    = int(SIZE * 0.14)
BAR_BOT  = int(SIZE * 0.80)

for idx, bar in enumerate(bars):
    x0 = int(SIZE * bar['x'])
    h  = int(SIZE * bar['h'])
    y0 = BAR_BOT - h
    x1 = x0 + BAR_W

    # 바 본체 (약간 투명)
    alpha = 220 - idx * 15
    bar_img = Image.new('RGBA', (x1 - x0, BAR_BOT - y0), (*GREEN, alpha))
    img.paste(bar_img, (x0, y0), bar_img)

    # 바 상단 하이라이트
    hi = Image.new('RGBA', (x1 - x0, 6), (255, 255, 255, 70))
    img.paste(hi, (x0, y0), hi)

# ─── 6. 우측 상단 상승 화살표 (트렌드 강조) ──────────────────
arr_cx, arr_cy = int(SIZE * 0.80), int(SIZE * 0.22)
arr_r = int(SIZE * 0.085)

# 원형 배경
circle_img = Image.new('RGBA', (arr_r * 2, arr_r * 2), (0, 0, 0, 0))
cd = ImageDraw.Draw(circle_img)
cd.ellipse([0, 0, arr_r * 2 - 1, arr_r * 2 - 1], fill=(*GREEN, 200))
img.paste(circle_img, (arr_cx - arr_r, arr_cy - arr_r), circle_img)

# 화살표 ↑
aw = int(arr_r * 0.45)
ah = int(arr_r * 0.55)
ax, ay = arr_cx, arr_cy
draw.polygon([
    (ax, ay - ah),
    (ax - aw, ay),
    (ax - aw // 2, ay),
    (ax - aw // 2, ay + ah),
    (ax + aw // 2, ay + ah),
    (ax + aw // 2, ay),
    (ax + aw, ay),
], fill=(255, 255, 255, 240))

# ─── 7. 하단 베이스라인 ───────────────────────────────────────
draw.line(
    [(int(SIZE * 0.12), BAR_BOT), (int(SIZE * 0.75), BAR_BOT)],
    fill=(*GREEN, 150), width=3
)

# ─── 8. 마스크 재적용 (모서리 처리) ──────────────────────────
final = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
final.paste(img, (0, 0), mask)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
final.save(out)
print(f'✅  아이콘 저장: {out}')
