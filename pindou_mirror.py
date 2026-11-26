"""拼豆鏡子：把攝影機畫面即時變成拼豆（融合豆）圖。

操作鍵
  Esc / Q   離開
  + / -     豆子變多 / 變少（更清楚 / 更像拼豆）
  B         切換拼豆品牌（Perler / MARD）
  M         鏡像開關
  R         畫面旋轉 90 度（螢幕實體直放、但 Windows 沒設直向時用）
  C         切換攝影機
  F         全螢幕開關
  L         自動補光開關（展場偏暗時讓顏色不灰）
  N / P     下一個 / 上一個濾鏡
  A         濾鏡自動輪播開關（預設關，固定原色）
  H         顯示 / 隱藏操作說明
  U         輸入照片上傳密語（拍完沒有 QR code 時用）

濾鏡：原色、黑白、粉彩、霓虹、五種人格色。每個濾鏡都只用真實拼豆色號中的一組，
畫面永遠是「真的拼得出來」的拼豆圖。人格色取自五隻像素角色 GIF 的主要顏色。

測試用：pindou_mirror.exe --demo 影片或圖片路徑（沒有攝影機時用影片代替）

色號資料：maxcleme/beadcolors（MIT），實體拼豆取色。
"""
import csv
import ctypes
import json
import os
import sys
import time

import cv2
import numpy as np

APP = "拼豆鏡子"
WIN = "pindou"  # OpenCV 視窗名稱用 ASCII，避免中文標題亂碼
ASPECT = 16 / 9  # 直立畫面：高 / 寬

BRANDS = [("Perler", "perler.csv"), ("MARD", "mard.csv")]
COLS_MIN, COLS_MAX, COLS_STEP = 24, 90, 6

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# ---------- 檔案位置（原始碼與 PyInstaller 打包後都適用） ----------

def res_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

def app_dir():
    """設定檔與錯誤記錄的位置。
    Windows：exe 旁邊。Mac：.app 裡面不能寫，改放 ~/Library/Application Support/拼豆鏡子"""
    if IS_MAC and getattr(sys, "frozen", False):
        d = os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP)
        os.makedirs(d, exist_ok=True)
        return d
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

SETTINGS = os.path.join(app_dir(), "pindou_settings.json")
DEFAULTS = {"cols": 40, "brand": 0, "mirror": True, "rotate": 0, "camera": 0, "fullscreen": True, "boost": True,
            "filter": 0, "auto": False}
AUTO_SECONDS = 30
# 可切換的濾鏡（N / P）。11/26 客戶：不再自動輪播，每次開啟固定用第一個（原色）
ROTATION = ["原色", "浪漫曇阿鵝", "沙漠好奇浣熊"]
FILTER_LABEL_SECONDS = 3   # 工作人員按 N / P / A 後，上方顯示濾鏡名稱幾秒

# 背景：測驗網站的封面天空（11/26 客戶：取代粉紅外框和阿鵝），雲和海左右慢慢飄，跟網站封面一樣
BG_FILE = os.path.join("assets", "cover_bg.png")
BG_SPLIT = 0.608           # 圖片上方 60.8% 是天空、下面是海（同網站 .cover-sky / .cover-sea）
BG_ZOOM, BG_DRIFT = 1.06, 0.022     # 放大 6%，左右各飄 2.2%
BG_PERIOD = (16.0, 12.0)            # 天空、海飄一趟的秒數（海反方向）
# 拼豆窗格在 9:16 版面中的位置（沿用原本外框的窗格比例）
WIN_RECT = (0.16875, 0.09507, 0.834375, 0.913732)

# 拍照：比 5（張開手掌）維持一下就開始倒數，倒數完拍照存檔
PALM_HOLD = 0.7        # 手掌要維持幾秒才觸發，避免揮手誤觸
COUNTDOWN = 5          # 倒數秒數
RESULT_SECONDS = 30    # QR code（或失敗訊息）出現後顯示的秒數
UPLOAD_WAIT = 45       # 上傳最多等這麼久，超過就回到待機（照片仍在本機）
PHOTO_DIR = "photos"   # 照片存在 app_dir() 底下

def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s

def save_settings(s):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

# ---------- 色表 ----------

def load_palette(filename):
    """讀 beadcolors 的 raw csv：code,name,r,g,b,contributor"""
    codes, rgb = [], []
    with open(os.path.join(res_dir(), "palettes", filename), encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 5:
                continue
            name = row[1].lower()
            if any(w in name for w in ("clear", "glow", "glitter", "transparent")):
                continue  # 透明、夜光、亮粉在螢幕上沒有意義
            codes.append(row[0])
            rgb.append((int(row[2]), int(row[3]), int(row[4])))
    return codes, np.array(rgb, dtype=np.uint8)

def build_lut(rgb):
    """32×32×32 的 RGB → 色號索引對照表，用 Lab 色彩空間找最接近的豆子"""
    pal_lab = cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    steps = (np.arange(32) * 8 + 4).astype(np.uint8)
    grid = np.stack(np.meshgrid(steps, steps, steps, indexing="ij"), -1).reshape(1, -1, 3)
    grid_lab = cv2.cvtColor(grid, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    lut = np.empty(grid_lab.shape[0], dtype=np.int32)
    for i in range(0, grid_lab.shape[0], 4096):
        d = ((grid_lab[i:i + 4096, None, :] - pal_lab[None, :, :]) ** 2).sum(-1)
        lut[i:i + 4096] = d.argmin(1)
    return lut.reshape(32, 32, 32)

# ---------- 濾鏡（每個濾鏡＝一組真實色號） ----------

PERSONA_ORDER = ["G", "F", "R", "E", "H"]

def load_personas():
    try:
        with open(os.path.join(res_dir(), "palettes", "personas.json"), encoding="utf-8") as f:
            d = json.load(f)
        return [(d[t]["name"], d[t]["colors"]) for t in PERSONA_ORDER if t in d]
    except (OSError, ValueError, KeyError):
        return []

def filter_names(personas):
    return ["原色", "黑白", "粉彩", "霓虹"] + [n for n, _ in personas]

def filter_subset(rgb, fi, personas):
    """回傳這個濾鏡可用的色號索引（指向完整色表）"""
    lab = cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    L = lab[:, 0] * 100 / 255
    C = np.hypot(lab[:, 1] - 128, lab[:, 2] - 128)
    hsv = cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3).astype(np.float32) / 255
    darkest = int(L.argmin())
    if fi == 0:                                   # 原色
        sub = np.arange(len(rgb))
    elif fi == 1:                                 # 黑白：幾乎沒有彩度的豆子
        sub = np.where(C < 9)[0]
    elif fi == 2:                                 # 粉彩：亮、彩度中低
        sub = np.where((L > 68) & (C > 8) & (C < 48))[0]
    elif fi == 3:                                 # 霓虹：高飽和高亮度，加最黑的豆讓暗部是黑的
        sub = np.append(np.where((hsv[:, 1] > 0.6) & (hsv[:, 2] > 0.8))[0], darkest)
    else:                                         # 人格色：角色主要顏色對應到最接近的豆子
        anchors = np.array(personas[fi - 4][1], np.uint8).reshape(1, -1, 3)
        a_lab = cv2.cvtColor(anchors, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
        sub = ((a_lab[:, None, :] - lab[None, :, :]) ** 2).sum(-1).argmin(1)
        if L[sub].min() > 30:                     # 沒有深色就補一顆最黑的，暗部才有層次
            sub = np.append(sub, darkest)
    sub = np.unique(sub)
    return sub if len(sub) >= 2 else np.arange(len(rgb))

# ---------- 背景（網站封面的天空與海） ----------

def load_background():
    p = os.path.join(res_dir(), BG_FILE)
    if not os.path.exists(p):
        return None
    from PIL import Image
    return np.array(Image.open(p).convert("RGB"))[..., ::-1].copy()   # BGR


_bg_cache = {}

def draw_background(img, bg, t):
    """把封面背景鋪滿整個畫面（等比放大裁切），天空和海各自左右飄"""
    H, W = img.shape[:2]
    key = (W, H)
    if key not in _bg_cache:
        _bg_cache.clear()
        bh, bw = bg.shape[:2]
        k = max(W / bw, H / bh) * BG_ZOOM
        big = cv2.resize(bg, (int(bw * k) + 2, int(bh * k) + 2), interpolation=cv2.INTER_NEAREST)
        _bg_cache[key] = big
    big = _bg_cache[key]
    BH, BW = big.shape[:2]
    ox, oy = (BW - W) // 2, (BH - H) // 2
    split = min(H, max(0, int(BH * BG_SPLIT) - oy))
    import math
    for (y0, y1), period, sign in (((0, split), BG_PERIOD[0], 1), ((split, H), BG_PERIOD[1], -1)):
        if y1 <= y0:
            continue
        e = (1 - math.cos(math.pi * ((t / period) % 2))) / 2       # 0→1→0，像 CSS ease-in-out alternate
        dx = int(round(sign * (e * 2 - 1) * BG_DRIFT * W))
        x = max(0, min(BW - W, ox + dx))
        img[y0:y1] = big[oy + y0:oy + y1, x:x + W]


_label_cache = {}

def label_image(text, height, bg, fg=(255, 255, 255)):
    """預先畫好的小標籤（避免每一格都用 Pillow 重畫整個畫面）"""
    key = (text, height, bg)
    if key not in _label_cache:
        from PIL import Image, ImageDraw, ImageFont
        font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
        size = max(12, int(height * 0.6))
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        w = int(tmp.textlength(text, font=font)) + height
        img = Image.new("RGB", (w, height), bg[::-1])
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, height - 1), radius=height // 2, fill=(16, 36, 58))
        d.text((height // 2, (height - size) // 2 - size // 10), text, font=font, fill=fg)
        _label_cache[key] = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return _label_cache[key]

# ---------- 手勢：比 5（張開手掌） ----------

class PalmDetector:
    """用 MediaPipe 手部骨架判斷畫面中有沒有人「五指張開」。載入失敗時自動停用，不影響拼豆畫面。"""

    TIPS, PIPS = (8, 12, 16, 20), (6, 10, 14, 18)

    def __init__(self):
        self.ok = False
        try:
            os.environ.setdefault("GLOG_minloglevel", "2")
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            model = os.path.join(res_dir(), "assets", "hand_landmarker.task")
            with open(model, "rb") as f:
                opts = vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_buffer=f.read()),
                    running_mode=vision.RunningMode.VIDEO, num_hands=2,
                    min_hand_detection_confidence=0.6, min_tracking_confidence=0.5)
            self.mp = mp
            self.lm = vision.HandLandmarker.create_from_options(opts)
            self.t0 = time.time()
            self.last_ts = -1
            self.ok = True
        except Exception as e:  # 沒有 mediapipe 或模型檔：只是不能用手勢拍照
            self.error = repr(e)

    @staticmethod
    def _d(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    def open_palm(self, frame_bgr):
        """回傳 True：畫面中有一隻手五指都張開"""
        if not self.ok:
            return False
        h, w = frame_bgr.shape[:2]
        small = cv2.resize(frame_bgr, (640, int(640 * h / w)))
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ts = int((time.time() - self.t0) * 1000)
        if ts <= self.last_ts:
            ts = self.last_ts + 1
        self.last_ts = ts
        res = self.lm.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ts)
        for p in res.hand_landmarks:
            wrist = p[0]
            # 四指：指尖離手腕比第二指節遠很多＝伸直（跟手的方向無關）
            fingers = sum(self._d(p[t], wrist) > self._d(p[j], wrist) * 1.25 for t, j in zip(self.TIPS, self.PIPS))
            # 拇指：指尖離小指根部，比拇指根部離小指根部遠＝張開
            thumb = self._d(p[4], p[17]) > self._d(p[2], p[17]) * 1.15
            if fingers == 4 and thumb:
                return True
        return False


def save_photo(img):
    d = os.path.join(app_dir(), PHOTO_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("拼豆_%Y%m%d_%H%M%S.jpg"))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
        buf.tofile(path)  # tofile 才支援中文路徑
    return path


def text_image(lines, size, fg=(255, 255, 255), bg=(58, 36, 16), pad=None, radius=None):
    """多行文字的小圖（BGR），預先畫好再貼，避免每一格重畫整個畫面。fg / bg 都是 BGR（預設深藍底白字）"""
    from PIL import Image, ImageDraw, ImageFont
    font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    pad = int(size * 0.45) if pad is None else pad
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w = int(max(tmp.textlength(t, font=font) for t in lines)) + pad * 2
    lh = int(size * 1.35)
    h = lh * len(lines) + pad * 2
    im = Image.new("RGB", (w, h), bg[::-1])
    d = ImageDraw.Draw(im)
    if radius:
        im.paste(Image.new("RGB", (w, h), (0, 0, 0)))
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=bg[::-1])
    for i, t in enumerate(lines):
        tw = tmp.textlength(t, font=font)
        d.text(((w - tw) / 2, pad + i * lh), t, font=font, fill=fg[::-1])
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def outline_text(lines, size, fg=(255, 255, 255), stroke=(58, 36, 16)):
    """透明底的外框字（BGRA），直接疊在畫面上、沒有底色方塊。fg / stroke 是 BGR"""
    from PIL import Image, ImageDraw, ImageFont
    font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    sw = max(4, size // 12)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(max(tmp.textlength(t, font=font) for t in lines)) + sw * 4
    lh = int(size * 1.25)
    h = lh * len(lines) + sw * 4
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, t in enumerate(lines):
        tw = tmp.textlength(t, font=font)
        d.text(((w - tw) / 2, sw * 2 + i * lh), t, font=font, fill=fg[::-1] + (255,),
               stroke_width=sw, stroke_fill=stroke[::-1] + (255,))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGBA2BGRA)


def paste(img, sub, x, y):
    """把小圖貼到大圖上（自動裁掉超出範圍的部分）；sub 有透明通道（BGRA）時按透明度疊上去"""
    h, w = sub.shape[:2]
    H, W = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 > x0 and y1 > y0:
        part = sub[y0 - y:y1 - y, x0 - x:x1 - x]
        if part.shape[2] == 4:
            a = part[:, :, 3:4].astype(np.float32) / 255
            img[y0:y1, x0:x1] = (part[:, :, :3] * a + img[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)
        else:
            img[y0:y1, x0:x1] = part


_text_cache = {}

def text_cache(key, fn):
    if key not in _text_cache:
        if len(_text_cache) > 64:
            _text_cache.clear()
        _text_cache[key] = fn()
    return _text_cache[key]


def bundle_dir():
    """Mac：PindouMirror.app 所在的資料夾（.app/Contents/MacOS/執行檔 往上四層）；其他平台回傳 None"""
    if IS_MAC and getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        app = os.path.dirname(os.path.dirname(os.path.dirname(exe)))
        if app.endswith(".app"):
            return os.path.dirname(app)
    return None


def load_upload_config():
    """照片上傳設定 {url, key}，依序找：
    1. app_dir() 的 photo_upload.json（Windows＝exe 旁邊；Mac＝~/Library/Application Support/拼豆鏡子）
    2. Mac：PindouMirror.app 旁邊的 photo_upload.json
    3. 打包進去的 secrets/（只有 Windows 本機打包有）
    密語不放在公開程式碼，也不放進 GitHub 打包（公開 repo 的打包結果任何人都能下載）。"""
    paths = [os.path.join(app_dir(), "photo_upload.json")]
    if bundle_dir():
        paths.append(os.path.join(bundle_dir(), "photo_upload.json"))
    paths.append(os.path.join(res_dir(), "secrets", "photo_upload.json"))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("url") and c.get("key"):
                return c
        except (OSError, ValueError):
            pass
    return None


UPLOAD = load_upload_config()
UPLOAD_URL = UPLOAD["url"] if UPLOAD else ""
# 「拼豆照片」部署的網址可以公開：沒有密語就上傳不了。密語只由工作人員第一次開程式時貼上，不放進程式碼。
DEFAULT_UPLOAD_URL = ("https://script.google.com/macros/s/AKfycbzvYzvP9blWK7hkWLqUnzsWLXdghzemQu8N9DjtbHr3sGUoozez4"
                      "SEH3ohaA60leK4IOg/exec")


def ask_text(prompt):
    """跳出輸入框讓工作人員貼上文字；按略過／取消回傳 None"""
    import subprocess
    try:
        if IS_MAC:
            lines = ["on run argv", "activate",
                     'set r to display dialog (item 1 of argv) default answer "" with title (item 2 of argv) '
                     'buttons {"略過", "確定"} default button "確定" cancel button "略過"',
                     "return text returned of r", "end run"]
            cmd = ["osascript"]
            for line in lines:
                cmd += ["-e", line]
            p = subprocess.run(cmd + [prompt, APP], capture_output=True, text=True, timeout=900)
            return p.stdout.strip() or None if p.returncode == 0 else None
        if IS_WIN:
            ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
                  "[Microsoft.VisualBasic.Interaction]::InputBox($env:PINDOU_PROMPT, $env:PINDOU_TITLE, '')")
            p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=900, env=dict(os.environ, PINDOU_PROMPT=prompt, PINDOU_TITLE=APP),
                               creationflags=0x08000000)               # 不跳出黑色主控台視窗
            return p.stdout.strip() or None
    except Exception as e:
        log_error("輸入框失敗：" + repr(e))
    return None


def ssl_context():
    """HTTPS 用的憑證。Mac 打包版的 Python 讀不到系統憑證，不指定會 CERTIFICATE_VERIFY_FAILED"""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def upload_error_text(e):
    """上傳失敗時畫面上顯示的簡短原因"""
    s = str(e)
    if "forbidden" in s:
        return "密語錯誤，請按 U 重新輸入"
    if "CERTIFICATE" in s or "SSL" in s:
        return "安全連線失敗"
    if "timed out" in s or "URLError" in s or "getaddrinfo" in s or "Errno" in s:
        return "連不上網路"
    return "上傳失敗"


def check_key(url, key):
    """送一小段不是 JPG 的資料測試密語（photo 不能是空字串，否則會被當成測驗作答）：
    密語錯回 forbidden；密語對回 not jpg，雲端不會多出檔案。
    回傳 True／False；連不上網路或回應看不懂回傳 None。"""
    import urllib.request
    try:
        req = urllib.request.Request(url, data=json.dumps({"photo": "eA==", "key": key}).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "text/plain;charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as res:
            r = json.loads(res.read().decode("utf-8"))
        return {"not jpg": True, "forbidden": False}.get(r.get("error"))
    except Exception as e:
        log_error("檢查密語失敗：" + repr(e))
        return None


def setup_upload():
    """請工作人員貼上照片上傳密語，確認正確後存到 app_dir()/photo_upload.json，之後不用再輸入"""
    global UPLOAD, UPLOAD_URL
    msg = ("請貼上照片上傳密語（開發人員私下提供）。\n\n"
           "按「略過」也能使用，但拍完不會出現 QR code；之後可以按 U 鍵重新輸入。")
    while True:
        key = ask_text(msg)
        if not key:
            return False
        key = key.strip()
        ok = check_key(DEFAULT_UPLOAD_URL, key)
        if ok is False:
            msg = "密語錯誤，請再貼一次（不要多複製到空白或其他文字）。\n\n按「略過」可以先跳過。"
            continue
        cfg = {"url": DEFAULT_UPLOAD_URL, "key": key}
        try:
            with open(os.path.join(app_dir(), "photo_upload.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except OSError as e:
            log_error("儲存上傳設定失敗：" + repr(e))
        UPLOAD, UPLOAD_URL = cfg, cfg["url"]
        if ok is None:
            show_error_dialog("目前連不上網路，無法確認密語是否正確，先存起來。\n"
                              "拍照後如果沒有出現 QR code，請檢查網路，或按 U 重新輸入密語。")
        return True


def start_upload(path, photo):
    """背景上傳照片，成功後把下載網址做成 QR（不卡住畫面）。
    每張照片一個 job：上傳很慢、已經換下一個人拍照時，舊照片的 QR 不能出現在新的畫面上。"""
    import threading
    job = object()
    photo.update(msg="照片上傳中…", job=job, pending=True)

    def work():
        try:
            done = {"qr": make_qr(upload_photo(path), 400)}
            remove_photo(path)                              # 已在雲端，現場電腦不留；上傳失敗的才留著
        except Exception as e:
            done = {"msg": "照片已存檔（上傳失敗）|" + upload_error_text(e)}
            log_error("上傳失敗：" + repr(e))
        if photo.get("job") is job:
            photo.update(done, pending=False, t=time.time())   # 從 QR 出現開始算顯示時間
    threading.Thread(target=work, daemon=True).start()


def remove_photo(path):
    try:
        os.remove(path)
    except OSError as e:
        log_error("刪除本機照片失敗：" + repr(e))


def upload_photo(path):
    """傳到統計後台（Google Apps Script），回傳照片的下載網址。Google 偶爾回錯誤頁，最多試 3 次。"""
    import base64
    import urllib.request
    with open(path, "rb") as f:
        data = f.read()
    body = json.dumps({"photo": base64.b64encode(data).decode("ascii"), "key": UPLOAD["key"],
                       "name": os.path.basename(path)}).encode("utf-8")
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(UPLOAD["url"], data=body, method="POST",
                                         headers={"Content-Type": "text/plain;charset=utf-8"})
            with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as res:   # Apps Script 會轉址，urllib 自動跟隨
                r = json.loads(res.read().decode("utf-8"))
            if r.get("ok"):
                return r["url"]
            last = r.get("error")
            if last == "forbidden":
                break                                               # 密語錯，重試也沒用
        except Exception as e:
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"upload failed: {last}")


def log_error(msg):
    try:
        with open(os.path.join(app_dir(), "pindou_error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def make_qr(url, size):
    import qrcode
    q = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url)
    q.make(fit=True)
    im = np.array(q.make_image(fill_color="black", back_color="white").convert("RGB"))
    return cv2.resize(cv2.cvtColor(im, cv2.COLOR_RGB2BGR), (size, size), interpolation=cv2.INTER_NEAREST)

# ---------- 豆子外觀 ----------

BOARD = np.array([10, 10, 10], np.uint8)  # 豆子之間的空隙（模板底色），BGR；黑底

def make_sprites(rgb, size):
    """每個顏色預先畫好一顆豆：圓柱外圈、中間孔洞、左上亮面、右下陰影"""
    n = len(rgb)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    c = size / 2
    r = np.hypot(xx - c, yy - c)
    outer = size * 0.47
    hole = size * 0.17
    ring = (r <= outer) & (r >= hole)
    # 光影：左上亮、右下暗
    shade = 1.0 + 0.22 * (-(xx - c) - (yy - c)) / max(c, 1)
    shade -= 0.18 * np.clip((r - outer * 0.8) / (outer * 0.2), 0, 1)  # 外緣收暗
    shade -= 0.10 * np.clip((hole * 1.6 - r) / (hole * 0.6), 0, 1)    # 孔洞邊收暗
    bgr = rgb[:, ::-1].astype(np.float32)
    spr = np.empty((n, size, size, 3), np.uint8)
    spr[:] = BOARD
    hole_col = (BOARD.astype(np.float32) * 0.72).astype(np.uint8)
    for i in range(n):
        col = np.clip(bgr[i][None, None, :] * shade[..., None], 0, 255).astype(np.uint8)
        spr[i][ring] = col[ring]
        spr[i][r < hole] = hole_col
    return spr

# ---------- 畫面 ----------

def screen_size():
    try:
        if IS_WIN:
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        if IS_MAC:
            # CoreGraphics 主螢幕尺寸（點數，Retina 由系統放大，畫面一樣清楚）
            cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
            cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
            cg.CGDisplayPixelsWide.argtypes = cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            d = cg.CGMainDisplayID()
            return int(cg.CGDisplayPixelsWide(d)), int(cg.CGDisplayPixelsHigh(d))
    except Exception:
        pass
    return 1280, 800

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msjhbd.ttc", r"C:\Windows\Fonts\mingliu.ttc",  # Windows
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",                 # Mac
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
]

def show_error_dialog(msg):
    try:
        if IS_WIN:
            ctypes.windll.user32.MessageBoxW(None, msg, APP, 0x10)
        elif IS_MAC:
            import subprocess
            safe = msg.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display alert "{APP}" message "{safe}" as critical'], timeout=120)
    except Exception:
        pass

def crop_to_aspect(frame, aspect):
    """從中間裁出 高/寬 = aspect 的區域"""
    h, w = frame.shape[:2]
    if h / w < aspect:
        nw = int(h / aspect)
        x = (w - nw) // 2
        return frame[:, x:x + nw]
    nh = int(w * aspect)
    y = (h - nh) // 2
    return frame[y:y + nh]

def auto_light(img):
    """展場偏暗時把亮度拉起來，避免顏色都變成灰黑豆"""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l = lab[..., 0].astype(np.float32)
    mean = l.mean()
    if mean < 125:
        lab[..., 0] = np.clip(l * (125 / max(mean, 1)), 0, 255).astype(np.uint8)
        img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return img

def put_text_cjk(img, lines, org, scale=1.0):
    """OpenCV 不支援中文，用 Windows 字型透過 Pillow 畫；沒有 Pillow 時退回英文"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
        size = int(26 * scale)
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        x, y = org
        w = max(d.textlength(t, font=font) for t in lines) + 32
        h = len(lines) * (size + 10) + 24
        d.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=(16, 36, 58))
        for i, t in enumerate(lines):
            d.text((x + 16, y + 12 + i * (size + 10)), t, font=font, fill=(255, 255, 255))
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        return img

def open_camera(index):
    if IS_WIN:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    elif IS_MAC:
        cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap

# ---------- 主程式 ----------

def main():
    def arg(name):
        if name in sys.argv:
            i = sys.argv.index(name)
            return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        return None
    demo = arg("--demo")
    snapshot = arg("--snapshot")      # 測試用：不開視窗，跑幾格後把畫面存成圖片
    screen_arg = arg("--screen")      # 測試用：假裝螢幕尺寸，例如 1080x1920

    if "--checknet" in sys.argv:
        print("checknet", check_key(DEFAULT_UPLOAD_URL, "ci-wrong-key"), flush=True)
        return

    s = load_settings()
    if snapshot:
        s["fullscreen"] = True
        s["auto"] = False
    if arg("--filter") is not None:
        s["filter"] = int(arg("--filter"))
    personas = load_personas()
    if not (snapshot or demo) and not UPLOAD:
        setup_upload()                # 第一次開：請工作人員貼上照片上傳密語
    fnames = filter_names(personas)
    rotation = [fnames.index(n) for n in ROTATION if n in fnames] or [0]
    s["filter"] = int(s["filter"]) % len(fnames)
    if arg("--filter") is None:
        s["filter"] = rotation[0]          # 每次開啟都從原色開始（不沿用上次關掉時的濾鏡）
    s["auto"] = False                      # 不自動輪播；工作人員仍可按 A 暫時打開

    def step_filter(d):
        cur = rotation.index(s["filter"]) if s["filter"] in rotation else -1
        s["filter"] = rotation[(cur + d) % len(rotation)]

    palm = PalmDetector() if not (snapshot or demo) or "--gesture" in sys.argv else None
    photo = {"phase": "idle", "palm_since": None, "t": 0.0, "path": None, "qr": None, "msg": None,
             "job": None, "pending": False}
    # 測試用：--photo-phase countdown|result 直接看倒數／QR 畫面
    if arg("--photo-phase") == "countdown":
        photo.update(phase="countdown", t=time.time() - 2.2)
    elif arg("--photo-phase") == "result":
        photo.update(phase="result", t=time.time(), qr=make_qr("https://example.com/pindou/photo-test", 400))
    background = load_background()
    label_until = 0.0
    palettes, luts = {}, {}

    def palette(i):
        if i not in palettes:
            palettes[i] = load_palette(BRANDS[i][1])
        return palettes[i]

    def lut_for(bi, fi):
        if (bi, fi) not in luts:
            _, rgb = palette(bi)
            sub = filter_subset(rgb, fi, personas)
            luts[(bi, fi)] = (sub[build_lut(rgb[sub])], len(sub))
        return luts[(bi, fi)]

    sw, sh = screen_size()
    if screen_arg:
        sw, sh = (int(v) for v in screen_arg.lower().split("x"))
    if not snapshot:
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.setWindowTitle(WIN, APP)

    def apply_window():
        if snapshot:
            return
        if s["fullscreen"]:
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WIN, int(sh * 0.85 / ASPECT), int(sh * 0.85))
    apply_window()

    if demo and demo.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
        cap = cv2.VideoCapture()  # 圖片不用開影片
        still = cv2.imdecode(np.fromfile(demo, np.uint8), cv2.IMREAD_COLOR)  # 支援中文路徑
    elif demo:
        cap = cv2.VideoCapture(demo)
        still = None
    else:
        cap = open_camera(s["camera"])
        still = None

    smooth = None
    sprites_key, sprites = None, None
    help_until = time.time() + 6
    show_help = False
    last_cols = None
    frames_done = 0
    last_retry = time.time()
    last_switch = time.time()

    while True:
        # 取畫面
        if still is not None:
            ok, frame = True, still.copy()
        else:
            ok, frame = cap.read()
            if not ok and demo:  # 影片播完重頭
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()

        # 濾鏡自動輪播
        if s["auto"] and time.time() - last_switch >= AUTO_SECONDS and photo["phase"] == "idle":
            step_filter(1)
            last_switch = time.time()

        # 比 5 偵測：閒置時，手掌張開維持 PALM_HOLD 秒就開始倒數
        if palm and palm.ok and ok and frame is not None and photo["phase"] == "idle":
            if palm.open_palm(frame):
                photo["palm_since"] = photo["palm_since"] or time.time()
                if time.time() - photo["palm_since"] >= PALM_HOLD:
                    photo.update(phase="countdown", t=time.time(), palm_since=None)
            else:
                photo["palm_since"] = None

        # ---- 版面：輸出畫面 → 外框（9:16，等比放進畫面）→ 外框中間的窗格放拼豆 ----
        rot = s["rotate"] % 4
        if s["fullscreen"]:
            out_w, out_h = (sh, sw) if rot % 2 else (sw, sh)
        else:
            out_h = int(sh * 0.85)
            out_w = int(out_h / ASPECT)
        k = min(out_w, out_h / ASPECT)
        fw, fh = int(k), int(k * ASPECT)                  # 9:16 版面（文字、QR 的大小以它為準）
        fx, fy = (out_w - fw) // 2, (out_h - fh) // 2
        wx, wy = fx + int(WIN_RECT[0] * fw), fy + int(WIN_RECT[1] * fh)
        ww, wh = int((WIN_RECT[2] - WIN_RECT[0]) * fw), int((WIN_RECT[3] - WIN_RECT[1]) * fh)
        bg_col = (58, 36, 16)                              # 標籤底色（深藍）

        img = np.empty((out_h, out_w, 3), np.uint8)
        if background is not None:
            draw_background(img, background, time.time())
        else:
            img[:] = (235, 206, 160)
        bw_ = max(3, fw // 160)                            # 拼豆窗格的白色邊
        img[wy - bw_:wy + wh + bw_, wx - bw_:wx + ww + bw_] = 255
        img[wy:wy + wh, wx:wx + ww] = BOARD

        cols = int(s["cols"])
        bead = max(4, ww // cols)
        rows = max(1, wh // bead)
        codes, rgb = palette(s["brand"])
        lut, ncolors = lut_for(s["brand"], s["filter"])

        if not ok or frame is None:
            # 攝影機還沒好（Mac 等使用者按「允許」、或之後才插上）：每 2 秒重試
            if not demo and time.time() - last_retry > 2:
                last_retry = time.time()
                cap.release()
                cap = open_camera(s["camera"])
            hint = (["Mac：請在跳出的視窗按「允許」使用相機，", "或到「系統設定 → 隱私權與安全性 → 相機」打開拼豆鏡子"]
                    if IS_MAC else ["請確認攝影機已插上，", "並在「設定 → 隱私權 → 相機」允許桌面應用程式使用"])
            img = put_text_cjk(img, ["找不到攝影機（會自動重試）"] + hint + ["按 C 切換攝影機、Q 離開"],
                               (wx + 12, wy + 40), scale=max(0.5, ww / 1100))
        else:
            if s["mirror"] and not demo:
                frame = cv2.flip(frame, 1)
            frame = crop_to_aspect(frame, rows / cols)
            if s["boost"]:
                frame = auto_light(frame)
            small = cv2.resize(frame, (cols, rows), interpolation=cv2.INTER_AREA).astype(np.float32)
            # 前後幀平均，減少豆子顏色閃爍
            if smooth is None or smooth.shape != small.shape or last_cols != cols:
                smooth = small
            else:
                smooth = smooth * 0.55 + small * 0.45
            last_cols = cols
            q = (np.clip(smooth, 0, 255).astype(np.uint8) >> 3).astype(np.intp)  # BGR, 0..31
            idx = lut[q[..., 2], q[..., 1], q[..., 0]]  # LUT 以 RGB 為索引
            key = (s["brand"], bead)
            if sprites_key != key:
                sprites, sprites_key = make_sprites(rgb, bead), key
            mosaic = sprites[idx].transpose(0, 2, 1, 3, 4).reshape(rows * bead, cols * bead, 3)
            # 窗格寬高不一定是豆子大小的整數倍，微幅縮放填滿窗格，避免邊緣留黑縫
            if mosaic.shape[:2] != (wh, ww):
                mosaic = cv2.resize(mosaic, (ww, wh), interpolation=cv2.INTER_LINEAR)
            img[wy:wy + wh, wx:wx + ww] = mosaic

        # 濾鏡名稱：只在工作人員切換濾鏡後顯示幾秒（或自動輪播打開時一直顯示）
        if s["auto"] or time.time() < label_until:
            lh = max(18, int((wy - fy) * 0.45))
            lab = label_image("濾鏡：" + fnames[s["filter"]] + ("" if s["auto"] else "（固定）"), lh, bg_col)
            lx = fx + (fw - lab.shape[1]) // 2
            ly = fy + (wy - fy - lh) // 2
            if lab.shape[1] < fw and ly >= 0:
                img[ly:ly + lh, lx:lx + lab.shape[1]] = lab

        # ---- 拍照流程：倒數 → 拍下（存乾淨的畫面，不含提示文字）→ 閃光 → 左下角顯示 QR ----
        now = time.time()
        unit = max(12, int(fw * 0.045))                      # 文字大小跟著外框寬度
        if photo["phase"] == "countdown" and now - photo["t"] >= COUNTDOWN:
            path = save_photo(img)
            photo.update(phase="flash", t=now, path=path, qr=None, msg="照片已存檔")
            if UPLOAD_URL:
                start_upload(path, photo)
            else:
                photo["msg"] = "照片已存檔|沒有 QR：請按 U 輸入上傳密語"
        if photo["phase"] == "flash" and now - photo["t"] >= 0.35:
            photo.update(phase="result", t=now)
        if photo["phase"] == "result" and now - photo["t"] >= (UPLOAD_WAIT if photo["pending"] else RESULT_SECONDS):
            photo.update(phase="idle", qr=None, msg=None, job=None, pending=False)

        if photo["phase"] == "countdown":
            n = COUNTDOWN - int(now - photo["t"])
            num = text_cache(("cd", n, unit), lambda: outline_text([str(n)], unit * 6))
            paste(img, num, wx + (ww - num.shape[1]) // 2, wy + (wh - num.shape[0]) // 2)
            tip = text_cache(("cdtip", unit), lambda: outline_text(["準備拍照！"], int(unit * 1.2)))
            paste(img, tip, wx + (ww - tip.shape[1]) // 2, wy + (wh + num.shape[0]) // 2 + unit)
        elif photo["phase"] == "flash":
            img = cv2.addWeighted(img, 0.25, np.full_like(img, 255), 0.75, 0)
        elif photo["phase"] == "result":
            # 左下角：QR（上傳完成時）或狀態文字
            qr_size = int(fw * 0.30)
            qx, qy = fx + int(fw * 0.04), fy + fh - qr_size - int(fh * 0.025)
            if photo["qr"] is not None:
                cap_img = text_cache(("qrcap", unit), lambda: text_image(["掃描下載照片", "（7 天內有效）"], int(unit * 0.85)))
                pad = max(6, qr_size // 20)
                img[qy - pad:qy + qr_size + pad, qx - pad:qx + qr_size + pad] = 255
                paste(img, cv2.resize(photo["qr"], (qr_size, qr_size), interpolation=cv2.INTER_NEAREST), qx, qy)
                paste(img, cap_img, qx + (qr_size - cap_img.shape[1]) // 2, qy - pad - cap_img.shape[0] - 4)
            else:
                msg = text_cache(("msg", photo["msg"], unit), lambda: text_image((photo["msg"] or "").split("|"), int(unit * 0.8)))  # | 分行
                paste(img, msg, qx, fy + fh - msg.shape[0] - int(fh * 0.03))
        elif palm and palm.ok:
            hint = text_cache(("hint", unit), lambda: text_image(["張開手掌比 5，倒數拍照"], int(unit * 0.8)))
            paste(img, hint, fx + int(fw * 0.04), fy + fh - hint.shape[0] - int(fh * 0.03))

        if show_help or time.time() < help_until:
            img = put_text_cjk(img, [
                f"{APP}　{BRANDS[s['brand']][0]}　濾鏡：{fnames[s['filter']]}（{ncolors} 色）　豆子 {cols} 顆寬",
                "+ / -  豆子變多 / 變少      B  切換品牌",
                "N / P  下一個 / 上一個濾鏡   A  自動輪播 " + ("開" if s["auto"] else "關"),
                "M  鏡像   R  旋轉   C  切換攝影機   L  補光",
                "空白鍵  倒數拍照（或對鏡頭張開手掌比 5）" + ("" if palm and palm.ok else "　※手勢辨識未啟用"),
                "F  全螢幕    H  說明    U  上傳密語    Q  離開",
            ], (20, 20), scale=max(0.6, min(out_w, out_h) / 1100))

        if rot:
            img = np.rot90(img, k=-rot).copy()
        if snapshot:
            frames_done += 1
            if frames_done >= 20:
                cv2.imwrite(snapshot, img)
                print("snapshot saved", snapshot, img.shape, "filter", fnames[s["filter"]], "colors", ncolors,
                      "beads", cols, "x", rows, "bead px", bead, "window", (ww, wh),
                      "gesture", (palm.ok if palm else "off"), getattr(palm, "error", ""),
                      "upload", bool(UPLOAD_URL))
                break
            help_until = 0 if "--nohelp" in sys.argv else help_until
            continue
        cv2.imshow(WIN, img)

        k = cv2.waitKey(1 if still is None else 30) & 0xFF
        if k in (27, ord("q"), ord("Q")):
            break
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break
        if k in (ord("+"), ord("=")):
            s["cols"] = min(COLS_MAX, cols + COLS_STEP)
        elif k in (ord("-"), ord("_")):
            s["cols"] = max(COLS_MIN, cols - COLS_STEP)
        elif k in (ord("b"), ord("B")):
            s["brand"] = (s["brand"] + 1) % len(BRANDS)
        elif k in (ord("n"), ord("N")):
            step_filter(1)
            last_switch = label_until = time.time()
            label_until += FILTER_LABEL_SECONDS
        elif k in (ord("p"), ord("P")):
            step_filter(-1)
            last_switch = label_until = time.time()
            label_until += FILTER_LABEL_SECONDS
        elif k == ord(" "):                       # 空白鍵：工作人員手動開始倒數拍照
            if photo["phase"] == "idle":
                photo.update(phase="countdown", t=time.time(), palm_since=None)
            continue
        elif k in (ord("a"), ord("A")):
            s["auto"] = not s["auto"]
            last_switch = label_until = time.time()
            label_until += FILTER_LABEL_SECONDS
        elif k in (ord("m"), ord("M")):
            s["mirror"] = not s["mirror"]
        elif k in (ord("r"), ord("R")):
            s["rotate"] = (rot + 1) % 4
        elif k in (ord("l"), ord("L")):
            s["boost"] = not s["boost"]
        elif k in (ord("h"), ord("H")):
            show_help = not show_help
        elif k in (ord("f"), ord("F")):
            s["fullscreen"] = not s["fullscreen"]
            apply_window()
        elif k in (ord("u"), ord("U")) and not snapshot:
            if s["fullscreen"]:                       # 先離開全螢幕，輸入框才不會被蓋住
                cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            setup_upload()
            apply_window()
        elif k in (ord("c"), ord("C")) and not demo:
            cap.release()
            for step in range(1, 5):  # 往下找下一台可用的攝影機
                cand = (s["camera"] + step) % 5
                cap = open_camera(cand)
                if cap.isOpened():
                    s["camera"] = cand
                    break
        else:
            continue
        if not snapshot:
            save_settings(s)
        help_until = time.time() + 3  # 改設定後短暫顯示目前狀態

    cap.release()
    if not snapshot:
        cv2.destroyAllWindows()
        save_settings(s)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 出錯時寫記錄檔，並用中文視窗提示（不要卡在看不懂的英文錯誤框）
        import traceback
        log = os.path.join(app_dir(), "pindou_error.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(sys.argv[1:]) + "\n" + traceback.format_exc() + "\n")
        except OSError:
            pass
        if "--snapshot" not in sys.argv:
            show_error_dialog("程式發生錯誤，已記錄在：\n" + log + "\n請把這個檔案傳給開發人員。")
        sys.exit(1)
