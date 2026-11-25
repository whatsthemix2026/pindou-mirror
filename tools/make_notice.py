"""產生拍照區的現場告示（A5 直式，200dpi），輸出 dist/拍照告示.png"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dist", "拍照告示.png")
W, H = 1165, 1654                       # A5 @ 200dpi
PINK, INK, MUTED, WHITE = (254, 170, 185), (16, 36, 58), (90, 104, 120), (255, 255, 255)
FB, FR = r"C:\Windows\Fonts\msjhbd.ttc", r"C:\Windows\Fonts\msjh.ttc"
f = lambda s, b=False: ImageFont.truetype(FB if b else FR, s)

img = Image.new("RGB", (W, H), WHITE)
d = ImageDraw.Draw(img)
d.rectangle((0, 0, W, 300), fill=PINK)
d.text((W // 2, 110), "拼豆鏡子", font=f(96, True), fill=INK, anchor="mm")
d.text((W // 2, 220), "把自己變成拼豆！", font=f(54, True), fill=INK, anchor="mm")

try:  # 阿鵝
    duck = Image.open(os.path.join(ROOT, "..", "mix-quiz-site", "assets", "characters", "R.png")).convert("RGBA")
    duck = duck.crop(duck.getchannel("A").getbbox())
    duck.thumbnail((240, 240), Image.NEAREST)
    img.paste(duck, (W - duck.width - 40, (300 - duck.height) // 2), duck)  # 放在粉紅標頭右側，不擋說明文字
except OSError:
    pass

y = 360
d.text((80, y), "拍照方式", font=f(58, True), fill=INK)
y += 100
for n, t in enumerate(["站到螢幕前，對鏡頭張開手掌「比 5」", "畫面倒數 5、4、3、2、1，拍照！", "用手機掃描左下角的 QR code 下載照片"], 1):
    d.ellipse((80, y, 150, y + 70), fill=INK)
    d.text((115, y + 35), str(n), font=f(44, True), fill=WHITE, anchor="mm")
    d.text((180, y + 8), t, font=f(44, True), fill=INK)
    y += 110

y += 40
d.rounded_rectangle((60, y, W - 60, H - 80), radius=28, outline=PINK, width=8)
d.text((100, y + 40), "照片與隱私", font=f(52, True), fill=INK)
notes = [
    "・平常的鏡頭畫面不錄影、不儲存，",
    "　只有倒數結束時會拍下一張照片。",
    "・照片會上傳到本專題的雲端空間，",
    "　拿到 QR code 連結的人都看得到。",
    "・約 7 天後連結失效並移到垃圾桶，",
    "　再過約 30 天徹底刪除。",
    "・不想入鏡，請在倒數時離開畫面。",
    "・想提早刪除照片，請告訴現場工作人員。",
]
ny = y + 130
for t in notes:
    d.text((100, ny), t, font=f(40), fill=INK)
    ny += 62

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, dpi=(200, 200))
print("saved", OUT, "content bottom", ny, "box bottom", H - 80)
