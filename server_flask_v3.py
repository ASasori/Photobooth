import cv2
import os
import math
import mediapipe as mp
import numpy as np
import atexit
import json
import requests
import threading
import time
import RPi.GPIO as GPIO
from gpiozero import DistanceSensor
from flask import Flask, render_template
from flask_socketio import SocketIO, emit


# --- CONFIGURATION ---
HOST = "0.0.0.0"  # Host IP to run the server on (0.0.0.0 = listen on all available IPs)
PORT = 5000       # Port to run the server on
API_FILTER_URL = "https://photobooth.kazekageiii.xyz/api/filters"  # API endpoint to fetch filters
MOCK_JSON_PATH = "mock_filters.json"    # Fallback file if API is unreachable
USE_LOCAL_MOCK_ON_FAIL = True           # Flag to control fallback behavior
BUTTON_PIN = 17 
TRIG_PIN = 23
ECHO_PIN = 24
IDLE_TIMEOUT = 30 
DETECTION_RANGE_M = 1

# --- LOGGING COLORS ---
RED = '\033[91m'
GREEN = '\033[92m'
BLUE = '\033[94m'
ENDC = '\033[0m'

# --- GLOBAL VARIABLES ---
MAIN_DIR = os.path.dirname(os.path.abspath(__file__)) # Project's root directory
FILTER_LIBRARY = {}           # In-memory cache for loaded filter images and configs
latest_frame = None           # Thread-safe global variable to hold the latest camera frame
latest_frame_lock = threading.Lock() # Lock to prevent race conditions when accessing latest_frame
latest_processed_frame = None # Thread-safe global for the latest processed frame
processed_frame_lock = threading.Lock() # Lock for latest_processed_frame
camera_running = True         # Flag to control the camera reading thread
current_mode = None           # The active filter mode (e.g., 'cute', 'cool', or None)
is_streaming = False          # Flag to control the AI processing and streaming thread

# --- APPLICATION SETUP ---
app = Flask(__name__) # Initialize the Flask application
app.config['SECRET_KEY'] = 'secret!' # Required for SocketIO
# Initialize SocketIO with 'socketio' for high-performance async operations
# and allow all origins ('*') to connect (e.g., the Display FE)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize MediaPipe Face Mesh
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(max_num_faces=4, min_detection_confidence=0.5)

# Initialize OpenCV Camera Capture
cap = cv2.VideoCapture(0)
# You can set cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280) etc. if needed

class ButtonHandler:
    # Add sensor_ref parameter to init (default is None to avoid errors if not provided)
    def __init__(self, pin, sensor_ref=None):
        self.pin = pin
        self.sensor_ref = sensor_ref  # Store reference to the sensor object
        self.running = True  
        self.DOUBLE_CLICK_DELAY = 0.5 
        
        # Setup GPIO (RPi.GPIO)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        
        print(f"[{GREEN}GPIO{ENDC}] Button initialized on GPIO {self.pin}. Linked to Sensor: {sensor_ref is not None}")

        self.thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.thread.start()

    # ... (Keep the _polling_loop function as is) ...
    def _polling_loop(self):
        """This function retains your original logic"""
        click_count = 0
        last_click_time = 0
        prev_state = GPIO.input(self.pin)

        while self.running:
            current_state = GPIO.input(self.pin)
            if prev_state == 0 and current_state == 1:
                click_count += 1
                last_click_time = time.time()
                time.sleep(0.05) 

            if click_count > 0:
                elapsed = time.time() - last_click_time
                if elapsed > self.DOUBLE_CLICK_DELAY:
                    if click_count == 1:
                        self._trigger_single_click()
                    elif click_count >= 2:
                        self._trigger_double_click()
                    click_count = 0

            prev_state = GPIO.input(self.pin) 
            time.sleep(0.01) 

    def _trigger_single_click(self):
        print(f"[{BLUE}BUTTON{ENDC}] Single Click -> Start Session")
        socketio.emit('button-status', {'active': True})
        
        # When the ON button is pressed, notify the Sensor to reset the countdown timer
        if self.sensor_ref:
            self.sensor_ref.set_system_state(True)
            print(f"[{GREEN}LINK{ENDC}] Sensor timer reset.")

    def _trigger_double_click(self):
        print(f"[{BLUE}BUTTON{ENDC}] Double Click -> Stop Session")
        socketio.emit('button-status', {'active': False})
        
        # When the OFF button is pressed, notify the Sensor that the system is off (stop checking timeout)
        if self.sensor_ref:
            self.sensor_ref.set_system_state(False)
            print(f"[{GREEN}LINK{ENDC}] Sensor monitoring paused (System OFF).")

    def cleanup(self):
        self.running = False

# Ultrasonic Sensor Class
class UltrasonicMonitor:
    def __init__(self, trig_pin, echo_pin):
        self.running = True
        self.last_activity_time = time.time()
        self.system_is_active = False

        # Initialize sensor using gpiozero
        # max_distance: Maximum distance to measure (to filter noise)
        # threshold_distance: Activation threshold (used for events if needed)
        try:
            self.sensor = DistanceSensor(echo=echo_pin, trigger=trig_pin, 
                                         max_distance=3.0, threshold_distance=DETECTION_RANGE_M)
            print(f"[{GREEN}SENSOR{ENDC}] Ultrasonic Sensor (gpiozero) initialized.")
        except Exception as e:
            print(f"[{RED}ERROR{ENDC}] Failed to init sensor: {e}")
            self.running = False
            return

        # Start the 2-minute logic monitoring thread
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def _monitor_loop(self):
        """Loop to check timeout logic"""
        while self.running:
            
            if not self.system_is_active:
                time.sleep(0.05)
                continue
            # gpiozero returns distance in METERS (float)
            # If nothing is detected or it's too far, it usually returns max_distance
            
            try: 
                current_distance = self.sensor.distance 
                print(f"Distance: {current_distance:.2f} m", flush=True)

                if current_distance < DETECTION_RANGE_M:
                    # Person detected within 2m range
                    self.last_activity_time = time.time()
                    
                    # (Optional) Auto wake up if currently off
                    # if not self.system_is_active:
                    #     socketio.emit('button-status', {'active': True})
                    #     self.system_is_active = True

                else:
                    # No person detected -> Check timeout
                    elapsed_idle = time.time() - self.last_activity_time
                    
                    if elapsed_idle > IDLE_TIMEOUT and self.system_is_active:
                        print(f"[{RED}AUTO-OFF{ENDC}] No user for {IDLE_TIMEOUT}s -> Shutdown System")
                        socketio.emit('button-status', {'active': False})
                        self.system_is_active = False

                # Sleep 1 second
                time.sleep(1)
            except Exception as e:
                print(f"Sensor Error: {e}")
                time.sleep(1)

    def set_system_state(self, is_active):
        """Update state from Button (to synchronize logic)"""
        self.system_is_active = is_active
        if is_active:
            self.last_activity_time = time.time() # Reset timer when manually turned on via button

    def cleanup(self):
        self.running = False
        self.sensor.close() # Release gpiozero resources

# --- RESOURCE LOADING ---
def load_resources():
    """
    Loads filter configurations from the API or a local JSON file.
    This function populates the global FILTER_LIBRARY.
    Logic:
    1. Try to fetch from API_FILTER_URL.
    2. If fails AND USE_LOCAL_MOCK_ON_FAIL is True, load from MOCK_JSON_PATH.
    3. Load all filter images into RAM (FILTER_LIBRARY).
    """
    global FILTER_LIBRARY
    data_list = []
    
    # 1. Attempt to load data from API
    try:
        resp = requests.get(API_FILTER_URL, timeout=2)
        if resp.status_code == 200:
            data_list = resp.json().get("data", [])
            print(f"[{GREEN}INFO{ENDC}] Successfully loaded {len(data_list)} filters from API.")
    except Exception as e:
        print(f"[{RED}WARN{ENDC}] API request failed: {e}. Trying local fallback.")
    
    # 2. If API failed, try local mock file
    if not data_list and USE_LOCAL_MOCK_ON_FAIL:
        try:
            mock_path = os.path.join(MAIN_DIR, MOCK_JSON_PATH)
            with open(mock_path, 'r') as f:
                data_list = json.load(f).get("data", [])
            print(f"[{GREEN}INFO{ENDC}] Successfully loaded {len(data_list)} filters from local mock file.")
        except Exception as e:
            print(f"[{RED}ERROR{ENDC}] Failed to load local mock file: {e}")
            return # Exit if we can't load anything

    # 3. Process all items (from API or mock) and load images into RAM
    for item in data_list:
        path = os.path.join(MAIN_DIR, item["imageUrl"])
        img = None
        if item["imageUrl"].startswith("http"):
            # TODO: Add logic to download images from http URLs
            print(f"[{RED}WARN{ENDC}] HTTP image loading not implemented. Skipping {item['id']}")
            pass
        elif os.path.exists(path):
            # Load local image file
            img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        else:
            print(f"[{RED}WARN{ENDC}] Local file not found: {path}")

        if img is not None:
            # Store the loaded image and its config in our library
            FILTER_LIBRARY[item["id"]] = {"image": img, "config": item}
            
    print(f"[{GREEN}INFO{ENDC}] Total {GREEN}{len(FILTER_LIBRARY)}{ENDC} filter assets loaded into RAM.")

# --- IMAGE PROCESSING HELPERS ---

def rotate_image(img, angle):
    """Rotates an image (with alpha) around its center without cropping."""
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    
    # Get rotation matrix
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    
    # Calculate new bounding box size
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    
    # Adjust rotation matrix to account for translation
    M[0, 2] += (new_w / 2) - center[0]
    M[1, 2] += (new_h / 2) - center[1]
    
    # Perform the rotation
    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0) # Use transparent black for border
    )
    return rotated

def overlay_transparent(background, overlay, x, y):
    """
    Blends a transparent (RGBA) overlay onto a background (BGR) image
    at position (x, y) using optimized NumPy operations.
    """
    h_bg, w_bg = background.shape[:2]
    h_ov, w_ov = overlay.shape[:2]
    
    # --- 1. Calculate clipping boundaries ---
    if x + w_ov <= 0 or y + h_ov <= 0 or x >= w_bg or y >= h_bg:
        return background # Overlay is completely off-screen

    # On-screen coordinates
    x1, y1 = max(x, 0), max(y, 0)
    x2, y2 = min(x + w_ov, w_bg), min(y + h_ov, h_bg)

    # Overlay coordinates (relative to overlay image)
    overlay_x1, overlay_y1 = max(0, -x), max(0, -y)
    overlay_x2, overlay_y2 = overlay_x1 + (x2 - x1), overlay_y1 + (y2 - y1)
    
    # --- 2. Ensure overlay has 4 channels (RGBA) ---
    if overlay.shape[2] == 3:
        # This is a fallback for JPGs, but filters should be PNGs
        b, g, r = cv2.split(overlay)
        alpha = np.ones(b.shape, dtype=b.dtype) * 255
        overlay = cv2.merge((b, g, r, alpha))

    # --- 3. NumPy-based alpha blending (Vectorized) ---
    
    # Get the relevant crop from both images
    overlay_crop = overlay[overlay_y1:overlay_y2, overlay_x1:overlay_x2]
    background_crop = background[y1:y2, x1:x2]

    # Extract alpha channel and normalize to 0.0-1.0
    # Shape: (h, w)
    alpha_s = overlay_crop[:, :, 3] / 255.0
    alpha_l = 1.0 - alpha_s

    # Add a 3rd dimension to alpha masks for broadcasting
    # Shape changes from (h, w) to (h, w, 1)
    alpha_s_3d = alpha_s[..., np.newaxis]
    alpha_l_3d = alpha_l[..., np.newaxis]

    # Perform vectorized blending:
    # (h,w,1) * (h,w,3) + (h,w,1) * (h,w,3)
    blended_crop = (alpha_s_3d * overlay_crop[:, :, :3]) + (alpha_l_3d * background_crop)

    # Place the blended crop back into the background
    background[y1:y2, x1:x2] = blended_crop
    
    return background

def apply_dynamic_filter(face_landmarks, image, filter_asset, cfg):
    """
    Applies a single filter_asset to a face based on its landmarks and config.
    Process:
    1. Get landmark coordinates (left, right, anchor) from config.
    2. Calculate scale based on distance between left/right landmarks.
    3. Calculate angle based on the same landmarks.
    4. Resize and rotate the filter asset.
    5. Calculate final position using the anchor landmark + offset.
    6. Overlay the filter onto the image.
    """
    h, w = image.shape[:2]
    
    # Get landmark indices from config, with fallbacks
    l_idx, r_idx = cfg.get("left_idx", 234), cfg.get("right_idx", 454)
    left, right = face_landmarks.landmark[l_idx], face_landmarks.landmark[r_idx]
    anchor = face_landmarks.landmark[cfg.get("anchor_idx", 10)]
    
    # Convert normalized landmarks to pixel coordinates
    lx, ly = int(left.x * w), int(left.y * h)
    rx, ry = int(right.x * w), int(right.y * h)
    ax, ay = int(anchor.x * w), int(anchor.y * h)
    
    # Calculate scale based on face width
    scale = cfg.get("scale", 1.0)
    face_width = math.hypot(rx - lx, ry - ly)
    filter_w = max(50, int(face_width * scale))
    # Maintain aspect ratio
    filter_h = max(20, int(filter_asset.shape[0] * filter_w / filter_asset.shape[1]))
    
    resized = cv2.resize(filter_asset, (filter_w, filter_h))
    
    # Calculate angle to tilt the filter with the face
    angle = math.degrees(math.atan2(ry - ly, rx - lx))
    rotated = rotate_image(resized, -angle)
    
    # Calculate final position
    x_pos = int(ax - rotated.shape[1] // 2)
    y_pos = int(ay - rotated.shape[0] // 2 + cfg.get("offset_y", 0))
    
    return overlay_transparent(image, rotated, x_pos, y_pos)

# --- BACKGROUND THREADS ---

def read_camera_thread():
    """
    A dedicated thread that *only* reads frames from the camera.
    This prevents the blocking `cap.read()` call from freezing the main
    SocketIO thread, ensuring low-latency frame access.
    """
    global latest_frame, camera_running
    while camera_running:
        ret, frame = cap.read()
        if ret:
            with latest_frame_lock:
                # Flip the frame for a "mirror" effect
                latest_frame = cv2.flip(frame, 1)
        else:
            # Sleep briefly if camera read fails
            socketio.sleep(0.1)

def stream_task():
    """
    The main processing thread, run by SocketIO.
    This thread:
    1. Runs only when `is_streaming` is True (saves CPU).
    2. Grabs the latest frame from `read_camera_thread`.
    3. If a `current_mode` is set, runs face detection.
    4. Scans FILTER_LIBRARY for all filters matching the `current_mode`.
    5. Applies all matching filters to all detected faces.
    6. Encodes the final image to JPG and emits it as binary data.
    """
    global is_streaming, current_mode, latest_processed_frame
    print(f"[{BLUE}STREAM{ENDC}] Processing thread started.")
    while True:
        # --- CPU-Saving Check ---
        # If not streaming, sleep for a longer time and skip processing.
        if not is_streaming:
            socketio.sleep(0.5)
            continue

        # Get the latest frame safely
        with latest_frame_lock:
            if latest_frame is None:
                socketio.sleep(0.01)
                continue
            img = latest_frame.copy()

        # --- AI Processing (Only if a mode is active) ---
        if current_mode:
            # Convert to RGB for MediaPipe
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)
            
            if res.multi_face_landmarks:
                # Loop over *all* filters in the library
                for f_id, data in FILTER_LIBRARY.items():
                    # If this filter's type matches the current mode, apply it
                    if data["config"].get("filterType") == current_mode:
                        # Apply this one filter to all detected faces
                        for fl in res.multi_face_landmarks:
                            img = apply_dynamic_filter(fl, img, data["image"], data["config"])

        # Save the processed frame for capture requests
        with processed_frame_lock:
            latest_processed_frame = img.copy()
        
        # --- Encoding and Emitting ---
        # Encode the image to JPEG
        success, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if success:
            # Emit the raw bytes over SocketIO to the connected client (Display)
            socketio.emit('video_frame', buffer.tobytes())
        
        # Sleep to control FPS (e.g., ~30 FPS)
        socketio.sleep(0.033)

# --- FLASK ROUTES & SOCKETIO EVENTS ---
@socketio.on('connect')
def on_connect():
    """Called when the Display FE successfully connects."""
    print(f"[{GREEN}INFO{ENDC}] Display FE connected.")

@socketio.on('start_stream')
def on_start_stream():
    """Enables video processing and streaming."""
    global is_streaming
    is_streaming = True
    print(f"[{BLUE}CMD{ENDC}] Received START_STREAM")
    emit('stream_status', {'active': True}) # Send confirmation back

@socketio.on('stop_stream')
def on_stop_stream():
    """Disables video processing to save CPU."""
    global is_streaming
    is_streaming = False
    print(f"[{BLUE}CMD{ENDC}] Received STOP_STREAM")
    emit('stream_status', {'active': False}) # Send confirmation back

@socketio.on('change_filter')
def on_change_filter(data):
    """Updates the current filter mode, based on 'filterType'."""
    global current_mode
    mode = data.get('filterType')
    if mode == 'off':
        current_mode = None
    else:
        current_mode = mode
    print(f"[{BLUE}CMD{ENDC}] Change Filter: {current_mode}")

@socketio.on('capture_frame')
def on_capture_frame():
    """
    Called by the Display FE.
    Grabs the most recent *processed* frame, encodes it as high-quality
    binary, and sends it back to the Display.
    """
    frame_to_send = None
    
    with processed_frame_lock:
        if latest_processed_frame is not None:
            frame_to_send = latest_processed_frame.copy()
    
    if frame_to_send is not None:
        # Encode as High Quality JPEG (95)
        success, buffer = cv2.imencode('.jpg', frame_to_send, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if success:
            # --- CHANGE: Send raw bytes, not Base64 ---
            emit('captured_frame', buffer.tobytes())
            # --- END CHANGE ---
            print(f"[{GREEN}CAPTURE{ENDC}] Sent 1 captured frame (binary) to Display.")
    else:
        print(f"[{RED}ERROR{ENDC}] Capture requested but no frame available.")

# --- CLEANUP ---
def cleanup():
    """Registered with `atexit` to run on script shutdown."""
    global camera_running
    camera_running = False
    cap.release()
    GPIO.cleanup()

atexit.register(cleanup)

# --- APPLICATION ENTRY POINT ---
if __name__ == '__main__':
    
    sensor_monitor = UltrasonicMonitor(trig_pin=TRIG_PIN, echo_pin=ECHO_PIN)
    button_handler = ButtonHandler(pin=BUTTON_PIN, sensor_ref=sensor_monitor)
    # 1. Start the camera reading thread in the background
    threading.Thread(target=read_camera_thread, daemon=True).start()
    
    # 2. Start the main processing/streaming thread as a background task
    socketio.start_background_task(stream_task)
    
    # 3. Load all filter resources into RAM
    load_resources()
    
    # 4. Start the Flask-SocketIO server
    print(f"[{GREEN}INFO{ENDC}] Python Server starting at http://{HOST}:{PORT}")
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
