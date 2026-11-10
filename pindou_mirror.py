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
  H         顯示 / 隱藏操作說明

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
DEFAULTS = {"cols": 48, "brand": 0, "mirror": True, "rotate": 0, "camera": 0, "fullscreen": True, "boost": True}

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

# ---------- 豆子外觀 ----------

BOARD = np.array([226, 230, 234], np.uint8)  # 豆子之間的空隙（模板底色），BGR

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

    s = load_settings()
    if snapshot:
        s["fullscreen"] = True
    palettes = {}

    def palette(i):
        if i not in palettes:
            codes, rgb = load_palette(BRANDS[i][1])
            palettes[i] = (codes, rgb, build_lut(rgb))
        return palettes[i]

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

    while True:
        # 取畫面
        if still is not None:
            ok, frame = True, still.copy()
        else:
            ok, frame = cap.read()
            if not ok and demo:  # 影片播完重頭
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()

        # 依螢幕方向決定輸出畫布（螢幕直放就滿版，橫放就置中一個直立畫面）
        rot = s["rotate"] % 4
        out_w, out_h = (sh, sw) if rot % 2 else (sw, sh)
        if s["fullscreen"]:
            canvas_h = out_h
            # 直放的螢幕整個填滿；橫放（測試用筆電）就在中間放一個 9:16 直立畫面
            canvas_w = out_w if out_h >= out_w else int(out_h / ASPECT)
        else:
            canvas_h = int(sh * 0.85)
            canvas_w = int(canvas_h / ASPECT)
        aspect = canvas_h / canvas_w

        cols = int(s["cols"])
        bead = max(4, canvas_w // cols)
        rows = int(round(cols * aspect))
        codes, rgb, lut = palette(s["brand"])

        if not ok or frame is None:
            # 攝影機還沒好（Mac 等使用者按「允許」、或之後才插上）：每 2 秒重試
            if not demo and time.time() - last_retry > 2:
                last_retry = time.time()
                cap.release()
                cap = open_camera(s["camera"])
            img = np.zeros((canvas_h, canvas_w, 3), np.uint8)
            hint = (["Mac：請在跳出的視窗按「允許」使用相機，", "或到「系統設定 → 隱私權與安全性 → 相機」打開拼豆鏡子"]
                    if IS_MAC else ["請確認攝影機已插上，", "並在「設定 → 隱私權 → 相機」允許桌面應用程式使用"])
            img = put_text_cjk(img, ["找不到攝影機（會自動重試）"] + hint + ["按 C 切換攝影機、Q 離開"], (24, 40),
                               scale=max(0.6, canvas_w / 1100))
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
            tiles = sprites[idx]  # rows, cols, bead, bead, 3
            mosaic = tiles.transpose(0, 2, 1, 3, 4).reshape(rows * bead, cols * bead, 3)
            img = np.empty((canvas_h, canvas_w, 3), np.uint8)
            img[:] = BOARD
            oy = (canvas_h - mosaic.shape[0]) // 2
            ox = (canvas_w - mosaic.shape[1]) // 2
            mh, mw = min(mosaic.shape[0], canvas_h), min(mosaic.shape[1], canvas_w)
            img[max(oy, 0):max(oy, 0) + mh, max(ox, 0):max(ox, 0) + mw] = mosaic[:mh, :mw]

        if show_help or time.time() < help_until:
            img = put_text_cjk(img, [
                f"{APP}　品牌：{BRANDS[s['brand']][0]}（{len(codes)} 色）　豆子：{cols} 顆寬",
                "+ / -  豆子變多 / 變少      B  切換品牌",
                "M  鏡像      R  旋轉      C  切換攝影機",
                "F  全螢幕    L  自動補光   H  說明   Q  離開",
            ], (20, 20), scale=max(0.6, canvas_w / 1100))

        # 旋轉並放到整個螢幕中間
        if rot:
            img = np.rot90(img, k=-rot).copy()
        if s["fullscreen"]:
            screen = np.zeros((sh, sw, 3), np.uint8)
            screen[:] = (20, 16, 13)
            h, w = img.shape[:2]
            h, w = min(h, sh), min(w, sw)
            y, x = (sh - h) // 2, (sw - w) // 2
            screen[y:y + h, x:x + w] = img[:h, :w]
            img = screen
        if snapshot:
            frames_done += 1
            if frames_done >= 20:
                cv2.imwrite(snapshot, img)
                print("snapshot saved", snapshot, img.shape, "beads", cols, "x", rows, "bead px", bead, "colors", len(codes))
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
