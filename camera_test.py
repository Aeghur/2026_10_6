import cv2
import time
from pathlib import Path

dev = "/dev/video0"
output = Path(__file__).resolve().parent / "artifacts" / "captures" / "camera_test.jpg"
output.parent.mkdir(parents=True, exist_ok=True)

cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)

cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

print("opened:", cap.isOpened())
print("width:", cap.get(cv2.CAP_PROP_FRAME_WIDTH))
print("height:", cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print("fps:", cap.get(cv2.CAP_PROP_FPS))

# 给摄像头一点初始化时间
time.sleep(1)

for i in range(10):
    ret, frame = cap.read()

    print("frame", i, "ret =", ret)

    if ret:
        print("shape =", frame.shape)
        cv2.imwrite(str(output), frame)
        print("saved", output)
        break

cap.release()
