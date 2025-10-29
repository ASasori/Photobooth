import cv2
import os
import math
import mediapipe as mp
import numpy as np
import atexit
from glob import glob
from datetime import datetime
from flask import Flask, Response, jsonify, render_template, send_from_directory

# Configuration Constants
HOST = "0.0.0.0"
PORT = 5000  # Standard Flask port
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
RED = '\033[91m'
GREEN = '\033[92m'
BLUE = '\033[94m'
ENDC = '\033[0m'

# global variables for frame processing
frame_counter = 0
DETECTION_SKIP_FRAMES = 2  # Process face detection every 5th frame
results = None             # Store the last known MediaPipe results

# Directory paths setup
MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(MAIN_DIR, "saved_image")
FILTER_DIR = os.path.join(MAIN_DIR, "filters")
INDEX_HTML_RELATIVE = "templates/index_flask.html"
os.makedirs(SAVE_DIR, exist_ok=True)  # Ensure the save directory exists

print(f"[{GREEN}INFO{ENDC}] Main Directory: {MAIN_DIR}")
print(f"[{GREEN}INFO{ENDC}] Save Directory: {SAVE_DIR}")
print(f"[{GREEN}INFO{ENDC}] Filter Directory: {FILTER_DIR}")
print(f"[{GREEN}INFO{ENDC}] Index HTML Path: {os.path.join(MAIN_DIR, INDEX_HTML_RELATIVE)}")

# Filter toggles state
state = {
    "mustache": False,
    "glasses": False,
    "pimp_hat": False,
    "cowboy_hat": False,
}

# Load filter images
stache = cv2.imread(os.path.join(FILTER_DIR, "mustache.png"), cv2.IMREAD_UNCHANGED)
glasses = cv2.imread(os.path.join(FILTER_DIR, "pixel-sunglasses.png"), cv2.IMREAD_UNCHANGED)
pimp_hat = cv2.imread(os.path.join(FILTER_DIR, "pimphat.png"), cv2.IMREAD_UNCHANGED)
cowboy_hat = cv2.imread(os.path.join(FILTER_DIR, "cowboyhat.png"), cv2.IMREAD_UNCHANGED)

if all(x is not None for x in (stache, glasses, pimp_hat, cowboy_hat)):
    print(f"[{GREEN}INFO{ENDC}] Filters loaded successfully.")

# Mediapipe setup
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(max_num_faces=5, min_detection_confidence=0.5, min_tracking_confidence=0.5)
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

if not cap.isOpened():
    print(f"[{RED}INFO{ENDC}] Cannot open camera. Please check index (0) or connections.")
    exit()

# Initialize Flask App
app = Flask(__name__, template_folder=MAIN_DIR)

# --- Helper functions---
def save_image(image):
    filename = datetime.now().strftime(
        os.path.join(SAVE_DIR, "photo_%Y%m%d_%H%M%S.png")
    )
    cv2.imwrite(filename, image)
    print(f"[{GREEN}INFO{ENDC}] Saved {filename}")

def rotate_image(img, angle):
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    
    # Rotation matrix
    M = cv2.getRotationMatrix2D(center, angle, 1.0)

    # Compute new bounding box
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    
    # Adjust rotation matrix to shift image center
    M[0, 2] += (new_w / 2) - center[0]
    M[1, 2] += (new_h / 2) - center[1]

    # Rotate with larger canvas
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
    # Calculate overlay and background coordinates
    x1, y1 = max(x, 0), max(y, 0)
    x2, y2 = min(x + w_ov, w_bg), min(y + h_ov, h_bg)
    overlay_x1, overlay_y1 = max(0, -x), max(0, -y)
    overlay_x2, overlay_y2 = overlay_x1 + (x2 - x1), overlay_y1 + (y2 - y1)
    # If overlay has no alpha channel, add one
    if overlay.shape[2] == 3:
        b, g, r = cv2.split(overlay)
        alpha = np.ones(b.shape, dtype=b.dtype) * 255
        overlay = cv2.merge((b, g, r, alpha))
    # Blend overlay with background
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
    # Get landmark positions
    left = face_landmarks.landmark[left_idx]
    right = face_landmarks.landmark[right_idx]
    anchor_idx = face_landmarks.landmark[anchor_idx]
    # Convert to pixel coordinates
    left_x, left_y = int(left.x*w), int(left.y*h)
    right_x, right_y = int(right.x*w), int(right.y*h)
    anchor_x, anchor_y = int(anchor_idx.x*w), int(anchor_idx.y*h)
    # Calculate filter size and angle
    filter_w = max(100, int(math.hypot(right_x - left_x, right_y - left_y) * scale))
    filter_h = max(20, int(filter.shape[0] * filter_w / filter.shape[1]))
    # Resize and rotate filter
    filter_resized = cv2.resize(filter, (filter_w, filter_h))
    angle = math.degrees(math.atan2(right_y - left_y, right_x - left_x))
    filter_rotated = rotate_image(filter_resized, -angle)
    # Calculate position to overlay
    x1 = int(anchor_x - filter_rotated.shape[1] // 2)
    y1 = int(anchor_y - filter_rotated.shape[0] // 2 + offset_y)
    return overlay_transparent(image, filter_rotated, x1, y1)

# --- Flask Video Generator ---
def gen_frames():
    """
    Generator function that encodes and yields frames in MJPEG format.
    Uses global variables for state and detection optimization.
    """
    global frame_counter
    global results
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Process frame
        image = cv2.flip(frame, 1)
        frame_counter += 1
        
        # Reduce detection frames
        if frame_counter > DETECTION_SKIP_FRAMES:
            # Only perform the expensive MediaPipe processing periodically
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            frame_counter = 0 # Reset counter

        # Apply filters based on global state
        if results and results.multi_face_landmarks:
            for fl in results.multi_face_landmarks:
                if state["mustache"]:
                    image = apply_filter(fl, image, scale=1.2, offset_y=40,
                                         filter=stache, left_idx=234, right_idx=454, anchor_idx=1)
                if state["glasses"]:
                    image = apply_filter(fl, image, scale=2.0, offset_y=20,
                                         filter=glasses, left_idx=33, right_idx=263, anchor_idx=168)
                # Hat exclusivity check
                if state["pimp_hat"] and not state["cowboy_hat"]:
                    image = apply_filter(fl, image, scale=1.8, offset_y=-100,
                                         filter=pimp_hat, left_idx=234, right_idx=454, anchor_idx=10)
                # Hat exclusivity check
                if state["cowboy_hat"] and not state["pimp_hat"]:
                    image = apply_filter(fl, image, scale=2.3, offset_y=-70,
                                         filter=cowboy_hat, left_idx=234, right_idx=454, anchor_idx=10)
        
        # --- Encoding and Yielding Frame ---
        _, buffer = cv2.imencode('.jpg', image)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')


# --- Flask Routes ---
@app.route('/')
def index():
    """Renders the HTML client."""
    return render_template(INDEX_HTML_RELATIVE)

@app.route('/video_feed')
def video_feed():
    """The MJPEG stream endpoint. The browser's <img> tag points here."""
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/command/<cmd>')
def handle_command(cmd):
    """
    Handles button press commands from the client via HTTP GET request, 
    including saving the current frame.
    """
    global state
    
    # Filter toggle commands
    if cmd in state:
        # Toggle the filter state
        state[cmd] = not state[cmd]
        
        # Handle hat exclusivity
        if cmd == "pimp_hat" and state["pimp_hat"]:
            state["cowboy_hat"] = False
        elif cmd == "cowboy_hat" and state["cowboy_hat"]:
            state["pimp_hat"] = False
            
    # Turn Off All command
    elif cmd == "off":
        for key in state:
            state[key] = False
            
    # Save Image command
    elif cmd == "save":
        # Capture the current frame outside the stream generator
        ret, frame = cap.read()
        if ret:
            # Mirror and process the single frame (as done in the generator)
            image = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            # *** Force detection for high-quality snapshot ***
            results_save = face_mesh.process(rgb)

            # Apply filters using the fresh results
            if results_save and results_save.multi_face_landmarks:
                for fl in results_save.multi_face_landmarks:
                    if state["mustache"]:
                        image_bgr = apply_filter(fl, image, scale=1.2, offset_y=40, filter=stache, left_idx=234, right_idx=454, anchor_idx=1)
                    if state["glasses"]:
                        image_bgr = apply_filter(fl, image, scale=2.0, offset_y=20, filter=glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    # Hat exclusivity check
                    if state["pimp_hat"] and not state["cowboy_hat"]:
                        image_bgr = apply_filter(fl, image, scale=1.8, offset_y=-100, filter=pimp_hat, left_idx=234, right_idx=454, anchor_idx=10)
                    # Hat exclusivity check
                    if state["cowboy_hat"] and not state["pimp_hat"]:
                        image_bgr = apply_filter(fl, image, scale=2.3, offset_y=-70, filter=cowboy_hat, left_idx=234, right_idx=454, anchor_idx=10)
            
            save_image(image_bgr)
            return jsonify({"status": "saved", "state": state})
        else:
            return jsonify({"status": "error", "message": "Could not capture frame to save."}), 500

    print(f"[{GREEN}INFO{ENDC}] Command received: {cmd}. New State: {state}")
    
    # Return the current state for the client to update buttons
    return jsonify({"status": "updated", "state": state})

@app.route('/get_state')
def get_current_state():
    """Returns the current filter state to sync UI on load."""
    return jsonify({"state": state})

@app.route('/get_latest_images')
def get_latest_images():
    """
    Scans the SAVE_DIR for .png files, sorts them by modification time,
    and returns a list of their relative filenames for the carousel.
    """
    search_path = os.path.join(SAVE_DIR, "*.png")
    all_files = glob(search_path)
    
    # Sort files by modification time (newest first)
    all_files.sort(key=os.path.getmtime, reverse=True)
    
    # Get only the filenames relative to the SAVE_DIR
    latest_images = [os.path.basename(f) for f in all_files]
    
    # Return up to the last 10 images
    return jsonify({"latest_images": latest_images[:10]})

@app.route('/saved_image/<filename>')
def serve_image(filename):
    """Route to serve saved images from the SAVE_DIR."""
    # Ensure only files from SAVE_DIR are served
    return send_from_directory(SAVE_DIR, filename)

# --- Cleanup Function ---
def cleanup_resources():
    """Releases the camera and closes all OpenCV windows."""
    print(f"\n[{BLUE}INFO{ENDC}] Releasing camera and cleaning up resources...")
    cap.release()
    cv2.destroyAllWindows()

# Register the cleanup function to run upon program exit
atexit.register(cleanup_resources)

if __name__ == '__main__':
    try:
        print(f"Flask MJPEG Server started on http://{HOST}:{PORT}")
        print("Navigate to this address in your browser.")
        app.run(host=HOST, port=PORT, use_reloader=False, threaded=True)
    finally:
        print(f"\n[{BLUE}INFO{ENDC}] Keyboard Interrupt received. Shutting down server...")