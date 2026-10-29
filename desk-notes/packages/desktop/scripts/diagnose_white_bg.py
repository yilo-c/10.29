"""对比完美图片 vs 帧图片的边缘质量差异。"""

from PIL import Image
from pathlib import Path

PET = Path(__file__).resolve().parents[1] / "public" / "pet"
PERFECT = PET / "ChatGPT Image 2026年5月18日 04_59_20.png"
SAMPLES = [
    ("idle_01", PET / "xiaowen" / "frames" / "idle" / "idle_01.png"),
    ("click_defend_01", PET / "xiaowen" / "frames" / "click_defend" / "click_defend_01.png"),
    ("land_01", PET / "xiaowen" / "frames" / "land" / "land_01.png"),
    ("shy_01", PET / "xiaowen" / "frames" / "shy" / "shy_01.png"),
    ("combo3_01", PET / "xiaowen" / "frames" / "combo3" / "combo3_01.png"),
    ("drag_fly_01", PET / "xiaowen" / "frames" / "drag_fly" / "drag_fly_01.png"),
    ("notice_01", PET / "xiaowen" / "frames" / "notice" / "notice_01.png"),
    ("chat_open_01", PET / "xiaowen" / "frames" / "chat_open" / "chat_open_01.png"),
    ("sprite_idle", PET / "sprite_idle.png"),
    ("xiaowen_fullbody", PET / "xiaowen_fullbody.png"),
]


def edge_profile(path: Path) -> dict:
    """深度边缘分析：检测角色轮廓外的白色像素分布"""
    img = Image.open(path).convert("RGBA")
    pixels = img.load()
    w, h = img.size

    # 建立不透明掩码
    opaque = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if pixels[x, y][3] > 200:
                opaque[y][x] = True

    # 角色 bbox
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            if opaque[y][x]:
                min_x, min_y, max_x, max_y = min(min_x, x), min(min_y, y), max(max_x, x), max(max_y, y)

    if max_x < min_x:
        return {"error": "no opaque content"}

    # 找角色边缘像素（不透明但邻居有透明的）
    edge_pixels = []
    for y in range(max(0, min_y - 1), min(h, max_y + 2)):
        for x in range(max(0, min_x - 1), min(w, max_x + 2)):
            if not opaque[y][x]:
                continue
            is_edge = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and not opaque[ny][nx]:
                        is_edge = True
                        break
                if is_edge:
                    break
            if is_edge:
                edge_pixels.append((x, y))

    # 分析边缘像素外侧（角色外 1-5px）的像素
    outside_colors = {d: [] for d in range(1, 6)}
    for ex, ey in edge_pixels:
        for d in range(1, 6):
            for dy, dx in [(-d, 0), (d, 0), (0, -d), (0, d)]:
                nx, ny = ex + dx, ey + dy
                if 0 <= nx < w and 0 <= ny < h and not opaque[ny][nx]:
                    r, g, b, a = pixels[nx, ny]
                    outside_colors[d].append((r, g, b, a))

    # 统计外侧白色半透明像素
    result = {"edge_count": len(edge_pixels), "w": w, "h": h}
    for d in range(1, 6):
        pxs = outside_colors[d]
        if not pxs:
            result[f"d{d}_total"] = 0
            result[f"d{d}_white"] = 0
            result[f"d{d}_white_pct"] = 0.0
            continue
        white_count = sum(1 for r, g, b, a in pxs if min(r, g, b) > 200 and max(r, g, b) - min(r, g, b) < 35 and a > 0)
        result[f"d{d}_total"] = len(pxs)
        result[f"d{d}_white"] = white_count
        result[f"d{d}_white_pct"] = round(white_count / len(pxs) * 100, 1) if pxs else 0

    # 分析边缘像素本身的颜色（角色最外层的颜色）
    edge_white = 0
    edge_skin = 0
    edge_dark = 0
    edge_other = 0
    for ex, ey in edge_pixels:
        r, g, b, a = pixels[ex, ey]
        if min(r, g, b) > 200 and max(r, g, b) - min(r, g, b) < 35:
            edge_white += 1
        elif r > 180 and g > 140 and b > 120 and r > b:
            edge_skin += 1
        elif max(r, g, b) < 100:
            edge_dark += 1
        else:
            edge_other += 1

    result["edge_white"] = edge_white
    result["edge_skin"] = edge_skin
    result["edge_dark"] = edge_dark
    result["edge_other"] = edge_other

    return result


def main():
    print("=" * 105)
    print("  完美图片 vs 帧图片 — 边缘质量深度对比")
    print("=" * 105)
    print()

    # 先分析完美图片
    print(">>> 完美图片 (ChatGPT 原图)")
    p = edge_profile(PERFECT)
    if "error" not in p:
        print(f"  尺寸: {p['w']}x{p['h']},  边缘像素数: {p['edge_count']}")
        print(f"  边缘颜色分布: 白={p['edge_white']}  肤色={p['edge_skin']}  深色={p['edge_dark']}  其他={p['edge_other']}")
        for d in range(1, 6):
            total = p[f"d{d}_total"]
            white = p[f"d{d}_white"]
            pct = p[f"d{d}_white_pct"]
            bar = "#" * int(pct / 2)
            print(f"  外侧 {d}px: 总{total:>6}  白色={white:>5} ({pct:>5.1f}%) {bar}")
    print()

    # 分析帧图片
    print(">>> 帧图片对比")
    print("-" * 105)
    header = f"{'Name':<20} {'边缘白%':>7} {'外1px白%':>8} {'外2px白%':>8} {'外3px白%':>8} {'外4px白%':>8} {'外5px白%':>8}"
    print(header)
    print("-" * 105)

    for name, path in SAMPLES:
        if not path.exists():
            print(f"  {name}: NOT FOUND")
            continue
        p = edge_profile(path)
        if "error" in p:
            print(f"  {name}: {p['error']}")
            continue

        edge_w_pct = round(p["edge_white"] / p["edge_count"] * 100, 1) if p["edge_count"] else 0
        print(
            f"{name:<20} {edge_w_pct:>6.1f}% "
            f"{p['d1_white_pct']:>7.1f}% {p['d2_white_pct']:>7.1f}% "
            f"{p['d3_white_pct']:>7.1f}% {p['d4_white_pct']:>7.1f}% "
            f"{p['d5_white_pct']:>7.1f}%"
        )

    print()
    print("  解读:")
    print("    边缘白% = 角色最外层像素中白色(服装)的占比")
    print("    外Npx白% = 角色轮廓外第N层像素中白色半透明的占比")
    print("    如果外1-5px白色占比高 → 说明有白底残留/光晕")
    print("    如果外1-5px白色占比低 → 说明边缘干净")
    print("=" * 105)


if __name__ == "__main__":
    main()
