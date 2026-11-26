"""產生工作人員用的按鍵說明圖（A4 橫式，200dpi），輸出 dist/按鍵說明.png"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dist", "按鍵說明.png")
W, H = 2339, 1654                       # A4 橫式 @ 200dpi
PINK = (254, 170, 185)
PINK_LIGHT = (255, 232, 236)
INK = (16, 36, 58)
MUTED = (90, 104, 120)
WHITE = (255, 255, 255)
ACCENT = (0, 153, 219)                  # 阿鵝的藍

FONT_B = r"C:\Windows\Fonts\msjhbd.ttc"
FONT_R = r"C:\Windows\Fonts\msjh.ttc"
f = lambda size, bold=False: ImageFont.truetype(FONT_B if bold else FONT_R, size)

img = Image.new("RGB", (W, H), WHITE)
d = ImageDraw.Draw(img)

# ---- 標題列 ----
HEAD = 210
d.rectangle((0, 0, W, HEAD), fill=PINK)
d.text((80, 40), "拼豆鏡子　按鍵說明", font=f(80, True), fill=INK)
d.text((84, 142), "工作人員用　｜　Windows、Mac 按鍵相同", font=f(38), fill=INK)
try:  # 右上角放阿鵝（網站用的單獨像素角色圖，背景透明）
    duck = Image.open(os.path.join(ROOT, "..", "mix-quiz-site", "assets", "characters", "R.png")).convert("RGBA")
    duck = duck.crop(duck.getchannel("A").getbbox())
    duck.thumbnail((180, 180), Image.NEAREST)
    img.paste(duck, (W - duck.width - 90, (HEAD - duck.height) // 2), duck)
except OSError:
    pass

KEY_H, ROW_H = 84, 118
COL_W = (W - 80 * 2 - 60) // 2          # 兩欄
COLS = (80, 80 + COL_W + 60)


def keycap(x, y, label):
    font = f(40, True)
    tw = d.textlength(label, font=font)
    w = max(86, int(tw) + 44)
    d.rounded_rectangle((x + 5, y + 7, x + w + 5, y + KEY_H + 7), radius=14, fill=(180, 190, 200))
    d.rounded_rectangle((x, y, x + w, y + KEY_H), radius=14, fill=INK)
    d.text((x + (w - tw) / 2, y + 14), label, font=font, fill=WHITE)
    return x + w


def section(cx, y, title, rows, color):
    d.rounded_rectangle((cx, y, cx + COL_W, y + 70), radius=18, fill=color)
    d.text((cx + 36, y + 9), title, font=f(42, True), fill=INK)
    y += 98
    for keys, desc, note in rows:
        x = cx + 36
        for i, k in enumerate(keys):
            if i:
                d.text((x + 12, y + 20), "或", font=f(32), fill=MUTED)
                x += 60
            x = keycap(x, y, k)
        tx = cx + 330
        d.text((tx, y + (4 if note else 16)), desc, font=f(42, True), fill=INK)
        if note:
            d.text((tx + 2, y + 58), note, font=f(29), fill=MUTED)
        y += ROW_H
    return y + 24


left, right = COLS
y0 = HEAD + 50
section(left, y0, "常用", [
    (["Q", "Esc"], "離開程式", ""),
    (["H"], "顯示 / 隱藏操作說明", "剛打開時會自動顯示 6 秒"),
    (["F"], "全螢幕開關", ""),
], PINK)
section(left, y0 + 98 + 3 * ROW_H + 24, "畫面效果", [
    (["+"], "豆子變多", "人比較清楚"),
    (["-"], "豆子變少", "比較像拼豆"),
    (["N"], "下一個濾鏡", ""),
    (["P"], "上一個濾鏡", ""),
    (["A"], "濾鏡自動輪播 開 / 關", "預設開，每 30 秒換一個；關掉時上方顯示「（固定）」"),
    (["空白"], "倒數 5 秒拍照", "觀眾也可以對鏡頭張開手掌比 5"),
    (["B"], "切換拼豆品牌", "Perler（103 色）/ MARD（291 色）"),
], PINK)
yr = section(right, y0, "架設時才會用到", [
    (["M"], "鏡像開關", "預設開，像照鏡子"),
    (["R"], "畫面旋轉 90 度", "螢幕直放、但系統沒設成直向時用"),
    (["C"], "切換攝影機", "同時接了好幾台攝影機時用"),
    (["L"], "自動補光開關", "展場偏暗、顏色變灰時打開"),
], PINK_LIGHT)

# ---- 補充 ----
notes = [
    ("・", "濾鏡輪播：原色 → 浪漫曇阿鵝 → 沙漠好奇浣熊"),
    ("・", "拍完約 5～10 秒出現 QR code，顯示 30 秒（需要網路）"),
    ("・", "沒出現 QR：檢查網路，或按 U 重新輸入上傳密語"),
    ("・", "觀眾要刪照片：雲端「拼豆照片」依時間找，刪除並清空垃圾桶"),
    ("・", "比 5 沒反應：手要整隻入鏡、光線夠，或改按空白鍵"),
    ("・", "設定會自動記住，下次打開沿用"),
    ("・", "畫面卡住或怪怪的：按 Q 離開，再重新打開"),
    ("・", "找不到攝影機：確認 USB 插好，會每 2 秒自動重試"),
]
box_bottom = yr + 100 + len(notes) * 56 + 30   # 框剛好包住內容
d.rounded_rectangle((right, yr, right + COL_W, box_bottom), radius=24, outline=PINK, width=6)
d.text((right + 36, yr + 26), "補充", font=f(42, True), fill=INK)
ny = yr + 100
for bullet, t in notes:
    d.text((right + 40, ny), bullet, font=f(34), fill=ACCENT)
    d.text((right + 80, ny), t, font=f(34), fill=INK)
    ny += 56

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, dpi=(200, 200))
print("saved", OUT, img.size, "right column bottom", ny, "limit", H - 70)
