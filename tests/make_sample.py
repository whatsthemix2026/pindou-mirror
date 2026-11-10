"""產生一張測試用的彩色圖（不使用客戶素材），給自動打包後的冒煙測試用。"""
import sys

import cv2
import numpy as np

out = sys.argv[1] if len(sys.argv) > 1 else "sample.png"
h, w = 720, 1280
y, x = np.mgrid[0:h, 0:w].astype(np.float32)
img = np.stack([x / w * 255, y / h * 255, (1 - x / w) * 200 + 30], -1).astype(np.uint8)
cv2.circle(img, (640, 300), 150, (40, 180, 250), -1)   # 臉
cv2.rectangle(img, (480, 450), (800, 720), (200, 90, 40), -1)  # 身體
cv2.imwrite(out, img)
print("sample written", out)
