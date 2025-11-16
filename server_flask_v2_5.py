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
import threading  # For non-blocking camera feed

HOST = "0.0.0.0"
PORT = 5000
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
RED = '\033[91m'
GREEN = '\033[92m'
BLUE = '\033[94m'
ENDC = '\033[0m'

# --- Camera Threading Globals ---
latest_frame = None
latest_frame_lock = threading.Lock() # Lock to ensure thread-safe access to latest_frame
camera_running = True                # Flag to signal the camera thread to stop
# ----------------------------------

frame_counter = 0
DETECTION_SKIP_FRAMES = 2 # Process face mesh every N frames to save CPU
results = None

# --- File System Paths ---
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

# --- Load Filter Assets ---
print(f"[{BLUE}INFO{ENDC}] Loading filter assets...")
stache = cv2.imread(os.path.join(FILTER_DIR, "mustache.png"), cv2.IMREAD_UNCHANGED)
glasses = cv2.imread(os.path.join(FILTER_DIR, "pixel-sunglasses.png"), cv2.IMREAD_UNCHANGED)
pimp_hat = cv2.imread(os.path.join(FILTER_DIR, "pimphat.png"), cv2.IMREAD_UNCHANGED)
cowboy_hat = cv2.imread(os.path.join(FILTER_DIR, "cowboyhat.png"), cv2.IMREAD_UNCHANGED)
mickey = cv2.imread(os.path.join(FILTER_DIR, "mickey.png"), cv2.IMREAD_UNCHANGED)
mickey_glasses = cv2.imread(os.path.join(FILTER_DIR, "mickey-glasses.png"), cv2.IMREAD_UNCHANGED)
rabbit = cv2.imread(os.path.join(FILTER_DIR, "rabbit.png"), cv2.IMREAD_UNCHANGED)
angry = cv2.imread(os.path.join(FILTER_DIR, "angry.png"), cv2.IMREAD_UNCHANGED)

if all(x is not None for x in (stache, glasses, pimp_hat, cowboy_hat, mickey, mickey_glasses, rabbit, angry)):
    print(f"[{GREEN}INFO{ENDC}] All filters loaded successfully.")
else:
    print(f"[{RED}WARNING{ENDC}] One or more filter images failed to load.")

# --- Initialize MediaPipe Face Mesh ---
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(max_num_faces=4, min_detection_confidence=0.5, min_tracking_confidence=0.5)

# --- Initialize Camera Capture ---
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

if not cap.isOpened():
    print(f"[{RED}FATAL{ENDC}] Cannot open camera. Please check index (0) or connections.")
    exit()

app = Flask(__name__)

def read_camera_thread():
    """
    Reads frames from the camera in a separate thread.
    This prevents the main processing loop (gen_frames) from blocking
    on cap.read(), significantly reducing latency.
    """
    global latest_frame, camera_running, cap
    
    while camera_running:
        ret, frame = cap.read()
        if not ret:
            print(f"[{RED}ERROR{ENDC}] Camera feed lost. Stopping thread.")
            camera_running = False
            break
        
        # Flip the frame horizontally (selfie view)
        flipped_frame = cv2.flip(frame, 1)
        
        # Update the global frame buffer with thread-safe lock
        with latest_frame_lock:
            latest_frame = flipped_frame.copy()
            
    print(f"[{BLUE}INFO{ENDC}] Camera reading thread stopped.")


def save_image(image):
    """Saves a single image frame to the SAVE_DIR with a timestamp."""
    filename = datetime.now().strftime(
        os.path.join(SAVE_DIR, "photo_%Y%m%d_%H%M%S.png")
    )
    cv2.imwrite(filename, image)
    print(f"[{GREEN}INFO{ENDC}] Saved {filename}")

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


def apply_filter(face_landmarks, image, scale=1.2, offset_y=40,
                 filter=stache, left_idx=234, right_idx=454, anchor_idx=1):
    """
    Applies a filter asset to the image based on face landmark coordinates.
    - Calculates size based on left/right landmarks.
    - Calculates angle based on left/right landmarks.
    - Calculates position based on an anchor landmark.
    """
    h, w = image.shape[:2]
    
    # Get landmark coordinates
    left = face_landmarks.landmark[left_idx]
    right = face_landmarks.landmark[right_idx]
    anchor = face_landmarks.landmark[anchor_idx]
    
    # Convert normalized coords to pixel coords
    left_x, left_y = int(left.x*w), int(left.y*h)
    right_x, right_y = int(right.x*w), int(right.y*h)
    anchor_x, anchor_y = int(anchor.x*w), int(anchor.y*h)
    
    # --- Calculate filter size and angle ---
    # Use distance between left/right landmarks for scale
    filter_w = max(100, int(math.hypot(right_x - left_x, right_y - left_y) * scale))
    # Maintain aspect ratio
    filter_h = max(20, int(filter.shape[0] * filter_w / filter.shape[1]))
    
    filter_resized = cv2.resize(filter, (filter_w, filter_h))
    
    # Get rotation angle
    angle = math.degrees(math.atan2(right_y - left_y, right_x - left_x))
    filter_rotated = rotate_image(filter_resized, -angle)
    
    # --- Calculate filter position ---
    # Center the filter on the anchor point, then apply offset
    x1 = int(anchor_x - filter_rotated.shape[1] // 2)
    y1 = int(anchor_y - filter_rotated.shape[0] // 2 + offset_y)
    
    return overlay_transparent(image, filter_rotated, x1, y1)

def apply_color_tint(image, bgr_color, intensity=0.15):
    """Applies a uniform color tint to the image."""
    h, w, _ = image.shape
    color_overlay = np.full((h, w, 3), bgr_color, dtype=np.uint8)
    
    # Blend the original image with the color overlay
    alpha = 1.0 - intensity
    beta = intensity
    return cv2.addWeighted(image, alpha, color_overlay, beta, 0)

def apply_cool_tint(image, intensity=0.6):
    """Applies a desaturation/grayscale tint."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray_3c = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    
    alpha = 1.0 - intensity
    beta = intensity
    return cv2.addWeighted(image, alpha, gray_3c, beta, 0)

# --- Flask Video Generator ---
def gen_frames():
    """
    A generator function that yields processed video frames.
    This is used by Flask for the MJPEG stream.
    """
    global frame_counter
    global results
    global latest_frame
    
    # jpeg_params = [cv2.IMWRITE_JPEG_QUALITY, 85] # Slig
    
    while True:
        # Get the most recent frame from the camera thread
        with latest_frame_lock:
            if latest_frame is None:
                # Wait for the camera thread to provide the first frame
                continue
            image = latest_frame.copy()
        
        # --- Process the frame ---
        frame_counter += 1
        
        # Run expensive face detection only every N frames
        if frame_counter > DETECTION_SKIP_FRAMES:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            frame_counter = 0

        # Apply color tints
        if state["cute"]:
            tint_color = (200, 180, 255) # BGR: Pink/Magenta
            image = apply_color_tint(image, tint_color, intensity=0.2)    
        if state["cool"]:
            image = apply_cool_tint(image, intensity=0.5)
        if state["poetic"]:
            tint_color = (70, 110, 190) # BGR: Sepia/Yellow-ish
            image = apply_color_tint(image, tint_color, intensity=0.25)
        
        # Apply landmark-based filters
        if results and results.multi_face_landmarks:
            for fl in results.multi_face_landmarks:
                if state["cute"]:
                    # Landmark indices for glasses
                    image = apply_filter(fl, image, scale=2.0, offset_y=8,
                                         filter=mickey_glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    # Landmark indices for hat/ears
                    image = apply_filter(fl, image, scale=3, offset_y=-60,
                                         filter=mickey, left_idx=234, right_idx=454, anchor_idx=10)
                if state["cool"]:
                    # Landmark indices for mustache
                    image = apply_filter(fl, image, scale=1.2, offset_y=40,
                                         filter=stache, left_idx=234, right_idx=454, anchor_idx=1)
                    # Landmark indices for glasses
                    image = apply_filter(fl, image, scale=2.0, offset_y=20,
                                         filter=glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    # Landmark indices for hat
                    image = apply_filter(fl, image, scale=2.3, offset_y=-70,
                                         filter=cowboy_hat, left_idx=234, right_idx=454, anchor_idx=10)
                if state["poetic"] :
                    # Landmark indices for hat/ears
                    image = apply_filter(fl, image, scale=2.5, offset_y=-100,
                                         filter=rabbit, left_idx=234, right_idx=454, anchor_idx=10)
                    # Landmark indices for cheek/eye effect
                    image = apply_filter(fl, image, scale=1, offset_y=-50,
                                         filter=angry, left_idx=234, right_idx=454, anchor_idx=33)
                     
        # --- Encode and Yield Frame ---
        # _, buffer = cv2.imencode('.jpg', image, jpeg_params)
        _, buffer = cv2.imencode('.jpg', image)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template(INDEX_HTML_RELATIVE)

@app.route('/video_feed')
def video_feed():
    """Serves the MJPEG video stream."""
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/command/batch_upload', methods=['POST'])
def batch_upload():
    """
    Receives a batch of Base64 encoded images from the frontend,
    decodes them, and saves them to the SAVE_DIR.
    """
    try:
        data = request.get_json()
        if not data or 'images' not in data:
            return jsonify(status='error', message='No image data provided.'), 400

        base64_images = data['images']
        saved_count = 0

        for img_data_url in base64_images:
            # Split the Data URL (e.g., "data:image/jpeg;base64,")
            try:
                header, encoded_data = img_data_url.split(',', 1)
                img_data = base64.b64decode(encoded_data)
                
                # Generate a unique filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                
                # Determine file extension from header
                file_extension = ".jpg" # Default
                if "image/png" in header:
                    file_extension = ".png"

                filename = f"photobooth_{timestamp}{file_extension}"
                filepath = os.path.join(SAVE_DIR, filename)
                
                # Save the binary data
                with open(filepath, 'wb') as f:
                    f.write(img_data)
                
                print(f"[{GREEN}INFO{ENDC}] Saved {filename} from FE upload.")
                saved_count += 1
                
            except Exception as e:
                print(f"[{RED}ERROR{ENDC}] Failed to decode/save one image: {e}")
                pass # Skip corrupted/failed images and continue

        if saved_count == 0:
             return jsonify(status='error', message='Could not decode/save any images.'), 500

        return jsonify(status='saved', count=saved_count, message=f'Saved {saved_count} images.')

    except Exception as e:
        print(f"[{RED}ERROR{ENDC}] Error in batch_upload: {e}")
        return jsonify(status='error', message=str(e)), 500


@app.route('/command/<cmd>')
def handle_command(cmd):
    """Handles filter toggle commands from the frontend."""
    global state
    
    if cmd in state:
        # Toggle the specified filter and turn all others off
        current_val = state[cmd]
        for key in state:
            state[key] = False # Reset all
        state[cmd] = not current_val # Set the new value

    elif cmd == "off":
        # Turn all filters off
        for key in state:
            state[key] = False

    print(f"[{GREEN}INFO{ENDC}] Command received: {cmd}. New State: {state}")
    return jsonify({"status": "updated", "state": state})

@app.route('/get_state')
def get_current_state():
    """Returns the current filter state (cute, cool, poetic)."""
    return jsonify({"state": state})

@app.route('/get_latest_images')
def get_latest_images():
    """Returns a list of the 10 most recently saved images."""
    search_paths = [
        os.path.join(SAVE_DIR, "*.png"),
        os.path.join(SAVE_DIR, "*.jpg") # Include .jpg
    ]
    all_files = []
    for path in search_paths:
        all_files.extend(glob(path))
        
    # Sort by modification time (newest first)
    all_files.sort(key=os.path.getmtime, reverse=True)
    
    # Return just the filenames
    latest_images = [os.path.basename(f) for f in all_files]
    return jsonify({"latest_images": latest_images[:10]})

@app.route('/saved_image/<filename>')
def serve_image(filename):
    """Serves a specific image from the save directory."""
    return send_from_directory(SAVE_DIR, filename)

def cleanup_resources():
    """
    Cleans up resources on application exit.
    Signals the camera thread to stop and releases the camera.
    """
    global camera_running
    print(f"\n[{BLUE}INFO{ENDC}] Releasing camera and cleaning up resources...")
    camera_running = False # Signal the camera thread to exit its loop
    
    # Wait a brief moment for the thread to stop
    if 'cam_thread' in globals() and cam_thread.is_alive():
        cam_thread.join(timeout=0.5)
        
    cap.release()
    cv2.destroyAllWindows()
    print(f"[{BLUE}INFO{ENDC}] Cleanup complete.")

atexit.register(cleanup_resources)

if __name__ == '__main__':
    try:
        # Start the non-blocking camera reading thread
        cam_thread = threading.Thread(target=read_camera_thread)
        cam_thread.daemon = True # Allows app to exit even if thread is running
        cam_thread.start()
        print(f"[{GREEN}INFO{ENDC}] Camera reading thread started.")
        
        print(f"[{GREEN}INFO{ENDC}] Flask MJPEG Server started on http://{HOST}:{PORT}")
        print("Navigate to this address in your browser.")
        # 'threaded=True' is essential for handling concurrent requests
        app.run(host=HOST, port=PORT, use_reloader=False, threaded=True, debug=False)
    
    finally:
        # This block executes on KeyboardInterrupt (Ctrl+C)
        camera_running = False # Ensure thread is signaled to stop
        print(f"\n[{BLUE}INFO{ENDC}] Keyboard Interrupt received. Shutting down server...")


# ==== Websocket Commands Reference ====      
# start_stream: nhận từ fe qua websocket --> gửi video stream cho fe       
# stop_stream: nhận từ fe qua websocket --> dừng gửi video stream cho fe
# change_filter: nhận từ fe qua websocket --> thay đổi filter. data gồm: 
# + "type": "change_filter",
# + "data": { 
#            "filter_id": "uuid"
#           }
# khi khởi động gọi một api để lấy danh sách filter để áp dụng vào ảnh với dữ liệu trả về như sau:
# {
#     "data": [
#       {
#            "id": "2bb7c789-de1a-467e-8d14-4f36f3343828",
#            "imageUrl": "https://res.cloudinary.com/dtpqh6cau/image/upload/v1763213217/photoboth/filters/qwtppcnm9nkftfrdsrcv.jpg",
#            "publicId": "qwtppcnm9nkftfrdsrcv",
#            "filterType": "cool", // tên nhóm filter hiện có cute/cool/poetic
#            "scale": 2.5,
#            "offset_y": -100,
#            "anchor_idx": 10,
#            "left_idx": 10,
#            "right_idx": 10,
#            "type": "filter",
#            "createdAt": "2025-11-15T06:26:59.604Z",
#            "updatedAt": "2025-11-15T06:26:59.604Z"
#       }
#    ]
# }
# ======================================
