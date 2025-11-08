import cv2
import os
import math
import mediapipe as mp
import numpy as np
import atexit
from glob import glob
from datetime import datetime
from flask import Flask, Response, jsonify, render_template, send_from_directory, request 
import base64 
import re 

HOST = "0.0.0.0"
PORT = 5000
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
RED = '\033[91m'
GREEN = '\033[92m'
BLUE = '\033[94m'
ENDC = '\033[0m'

frame_counter = 0
DETECTION_SKIP_FRAMES = 2
results = None

MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(MAIN_DIR, "static", "saved_images")
FILTER_DIR = os.path.join(MAIN_DIR, "static", "images")
INDEX_HTML_RELATIVE = "index_flask_v2.html"
os.makedirs(SAVE_DIR, exist_ok=True)

print(f"[{GREEN}INFO{ENDC}] Main Directory: {MAIN_DIR}")
print(f"[{GREEN}INFO{ENDC}] Save Directory: {SAVE_DIR}")
print(f"[{GREEN}INFO{ENDC}] Filter Directory: {FILTER_DIR}")
print(f"[{GREEN}INFO{ENDC}] Index HTML Path: {os.path.join(MAIN_DIR, INDEX_HTML_RELATIVE)}")

# Filter toggles state
state = {
    "cute": False,
    "cool": False,
    "poetic": False,
}

# ... (Load filter images và Mediapipe setup giữ nguyên) ...
stache = cv2.imread(os.path.join(FILTER_DIR, "mustache.png"), cv2.IMREAD_UNCHANGED)
glasses = cv2.imread(os.path.join(FILTER_DIR, "pixel-sunglasses.png"), cv2.IMREAD_UNCHANGED)
pimp_hat = cv2.imread(os.path.join(FILTER_DIR, "pimphat.png"), cv2.IMREAD_UNCHANGED)
cowboy_hat = cv2.imread(os.path.join(FILTER_DIR, "cowboyhat.png"), cv2.IMREAD_UNCHANGED)
mickey = cv2.imread(os.path.join(FILTER_DIR, "mickey.png"), cv2.IMREAD_UNCHANGED)
mickey_glasses = cv2.imread(os.path.join(FILTER_DIR, "mickey-glasses.png"), cv2.IMREAD_UNCHANGED)
rabbit = cv2.imread(os.path.join(FILTER_DIR, "rabbit.png"), cv2.IMREAD_UNCHANGED)
angry = cv2.imread(os.path.join(FILTER_DIR, "angry.png"), cv2.IMREAD_UNCHANGED)

if all(x is not None for x in (stache, glasses, pimp_hat, cowboy_hat)):
    print(f"[{GREEN}INFO{ENDC}] Filters loaded successfully.")

mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(max_num_faces=4, min_detection_confidence=0.5, min_tracking_confidence=0.5)
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

if not cap.isOpened():
    print(f"[{RED}INFO{ENDC}] Cannot open camera. Please check index (0) or connections.")
    exit()

app = Flask(__name__)

# ... (Các hàm helper save_image, rotate_image, overlay_transparent, apply_filter giữ nguyên) ...
def save_image(image):
    filename = datetime.now().strftime(
        os.path.join(SAVE_DIR, "photo_%Y%m%d_%H%M%S.png")
    )
    cv2.imwrite(filename, image)
    print(f"[{GREEN}INFO{ENDC}] Saved {filename}")

def rotate_image(img, angle):
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    M[0, 2] += (new_w / 2) - center[0]
    M[1, 2] += (new_h / 2) - center[1]
    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0)
    )
    return rotated

def overlay_transparent(background, overlay, x, y):
    h_bg, w_bg = background.shape[:2]
    h_ov, w_ov = overlay.shape[:2]
    if x + w_ov <= 0 or y + h_ov <= 0 or x >= w_bg or y >= h_bg:
        return background
    x1, y1 = max(x, 0), max(y, 0)
    x2, y2 = min(x + w_ov, w_bg), min(y + h_ov, h_bg)
    overlay_x1, overlay_y1 = max(0, -x), max(0, -y)
    overlay_x2, overlay_y2 = overlay_x1 + (x2 - x1), overlay_y1 + (y2 - y1)
    if overlay.shape[2] == 3:
        b, g, r = cv2.split(overlay)
        alpha = np.ones(b.shape, dtype=b.dtype) * 255
        overlay = cv2.merge((b, g, r, alpha))
    alpha_s = overlay[overlay_y1:overlay_y2, overlay_x1:overlay_x2, 3] / 255.0
    alpha_l = 1.0 - alpha_s
    for c in range(3):
        background[y1:y2, x1:x2, c] = (
            alpha_s * overlay[overlay_y1:overlay_y2, overlay_x1:overlay_x2, c] +
            alpha_l * background[y1:y2, x1:x2, c]
        )
    return background

def apply_filter(face_landmarks, image, scale=1.2, offset_y=40,
                 filter=stache, left_idx=234, right_idx=454, anchor_idx=1):
    h, w = image.shape[:2]
    left = face_landmarks.landmark[left_idx]
    right = face_landmarks.landmark[right_idx]
    anchor_idx = face_landmarks.landmark[anchor_idx]
    left_x, left_y = int(left.x*w), int(left.y*h)
    right_x, right_y = int(right.x*w), int(right.y*h)
    anchor_x, anchor_y = int(anchor_idx.x*w), int(anchor_idx.y*h)
    filter_w = max(100, int(math.hypot(right_x - left_x, right_y - left_y) * scale))
    filter_h = max(20, int(filter.shape[0] * filter_w / filter.shape[1]))
    filter_resized = cv2.resize(filter, (filter_w, filter_h))
    angle = math.degrees(math.atan2(right_y - left_y, right_x - left_x))
    filter_rotated = rotate_image(filter_resized, -angle)
    x1 = int(anchor_x - filter_rotated.shape[1] // 2)
    y1 = int(anchor_y - filter_rotated.shape[0] // 2 + offset_y)
    return overlay_transparent(image, filter_rotated, x1, y1)

# --- Flask Video Generator (ĐÃ CẬP NHẬT) ---
def gen_frames():
    global frame_counter
    global results
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        image = cv2.flip(frame, 1)
        frame_counter += 1
        
        if frame_counter > DETECTION_SKIP_FRAMES:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            frame_counter = 0

        # ... (Phần áp dụng filter giữ nguyên) ...
        if results and results.multi_face_landmarks:
            for fl in results.multi_face_landmarks:
                if state["cute"]:
                    image = apply_filter(fl, image, scale=2.0, offset_y=8,
                                         filter=mickey_glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    image = apply_filter(fl, image, scale=3, offset_y=-60,
                                         filter=mickey, left_idx=234, right_idx=454, anchor_idx=10)
                if state["cool"]:
                    image = apply_filter(fl, image, scale=1.2, offset_y=40,
                                         filter=stache, left_idx=234, right_idx=454, anchor_idx=1)
                    image = apply_filter(fl, image, scale=2.0, offset_y=20,
                                         filter=glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    image = apply_filter(fl, image, scale=2.3, offset_y=-70,
                                         filter=cowboy_hat, left_idx=234, right_idx=454, anchor_idx=10)
                if state["poetic"] :
                    image = apply_filter(fl, image, scale=2.5, offset_y=-100,
                                         filter=rabbit, left_idx=234, right_idx=454, anchor_idx=10)
                    image = apply_filter(fl, image, scale=1, offset_y=-50,
                                         filter=angry, left_idx=234, right_idx=454, anchor_idx=33)
                    
                    
        
        # --- Encoding and Yielding Frame ---
        _, buffer = cv2.imencode('.jpg', image)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template(INDEX_HTML_RELATIVE)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# --- (MỚI) ROUTE ĐỂ NHẬN 4 ẢNH TỪ FE ---
@app.route('/command/batch_upload', methods=['POST'])
def batch_upload():
    """
    Nhận một loạt ảnh (Base64) từ FE, giải mã và lưu chúng.
    """
    try:
        data = request.get_json()
        if not data or 'images' not in data:
            return jsonify(status='error', message='No image data provided.'), 400

        base64_images = data['images']
        saved_count = 0

        for img_data_url in base64_images:
            # Tách phần header (ví dụ: "data:image/jpeg;base64,")
            try:
                # Tách header và data
                header, encoded_data = img_data_url.split(',', 1)
                # Giải mã Base64
                img_data = base64.b64decode(encoded_data)
                
                # Tạo tên file duy nhất
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                # Đuôi file nên dựa trên header, nhưng ở đây ta mặc định là .jpg
                file_extension = ".jpg"
                if "image/png" in header:
                    file_extension = ".png"

                filename = f"photobooth_{timestamp}{file_extension}"
                filepath = os.path.join(SAVE_DIR, filename)
                
                # Lưu file (chế độ "wb" - write binary)
                with open(filepath, 'wb') as f:
                    f.write(img_data)
                
                print(f"[{GREEN}INFO{ENDC}] Saved {filename} from FE upload.")
                saved_count += 1
                
            except Exception as e:
                print(f"[{RED}ERROR{ENDC}] Failed to decode/save one image: {e}")
                pass # Bỏ qua ảnh lỗi và tiếp tục

        if saved_count == 0:
             return jsonify(status='error', message='Could not decode/save any images.'), 500

        # Trả về thành công
        return jsonify(status='saved', count=saved_count, message=f'Saved {saved_count} images.')

    except Exception as e:
        print(f"[{RED}ERROR{ENDC}] Error in batch_upload: {e}")
        return jsonify(status='error', message=str(e)), 500


@app.route('/command/<cmd>')
def handle_command(cmd):
    global state
    
    if cmd in state:
        for key in state:
            if (key==cmd):
                state[cmd] = not state[cmd]
            else:
                state[key] = False

    elif cmd == "off":
        for key in state:
            state[key] = False

    print(f"[{GREEN}INFO{ENDC}] Command received: {cmd}. New State: {state}")
    return jsonify({"status": "updated", "state": state})

# ... (Các route /get_state, /get_latest_images, /saved_image/<filename> giữ nguyên) ...
@app.route('/get_state')
def get_current_state():
    return jsonify({"state": state})

@app.route('/get_latest_images')
def get_latest_images():
    search_paths = [
        os.path.join(SAVE_DIR, "*.png"),
        os.path.join(SAVE_DIR, "*.jpg") # Thêm .jpg
    ]
    all_files = []
    for path in search_paths:
        all_files.extend(glob(path))
        
    all_files.sort(key=os.path.getmtime, reverse=True)
    latest_images = [os.path.basename(f) for f in all_files]
    return jsonify({"latest_images": latest_images[:10]})

@app.route('/saved_image/<filename>')
def serve_image(filename):
    return send_from_directory(SAVE_DIR, filename)

# ... (Phần cleanup và main giữ nguyên) ...
def cleanup_resources():
    print(f"\n[{BLUE}INFO{ENDC}] Releasing camera and cleaning up resources...")
    cap.release()
    cv2.destroyAllWindows()

atexit.register(cleanup_resources)

if __name__ == '__main__':
    try:
        print(f"Flask MJPEG Server started on http://{HOST}:{PORT}")
        print("Navigate to this address in your browser.")
        app.run(host=HOST, port=PORT, use_reloader=False, threaded=True)
    finally:
        print(f"\n[{BLUE}INFO{ENDC}] Keyboard Interrupt received. Shutting down server...")