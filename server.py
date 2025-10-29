import cv2, os, math, asyncio, websockets, base64, json
import mediapipe as mp
import numpy as np
from glob import glob
from datetime import datetime
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# CONSTANTS
GREEN = '\033[92m'
BLUE = '\033[94m'
ENDC = '\033[0m'
HOST = "0.0.0.0"
PORT = 8765
# Directory paths setup
MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(MAIN_DIR, "saved_image")
FILTER_DIR = os.path.join(MAIN_DIR, "filters")
TEMPLATE_DIR = os.path.join(MAIN_DIR, "templates")

# Frame rate settings
frame_rate = {
    "60 fps": 0.016,
    "30 fps": 0.033,
}

# Filter toggles
state = {
    "mustache": False,
    "glasses": False,
    "pimp_hat": False,
    "cowboy_hat": False,
    "save": False,
}

# Filters
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
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# --- Helper functions ---
def get_latest_images(limit=10):
    images = sorted(
        glob(os.path.join(SAVE_DIR, "*.png")),
        key=os.path.getmtime,
        reverse=True
    )
    return images[:limit]

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
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

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

async def send_current_state(websocket):
    """Sends the current non-save state back to the client for UI sync."""
    global state
    
    await websocket.send(json.dumps({"current_state": {
        k: state[k] for k in state if k != 'save'
    }}))
    
async def process_command(websocket, data):
    """Processes a single command and sends back the updated state if filters change."""
    global state
    # Extract command
    command = data.get("command")
    if not command:
        return False # No valid command found
    # Handle get_latest command
    if command == "get_latest":
        files = get_latest_images()
        await websocket.send(json.dumps({"latest_images": files}))
        return False # Not a filter/state change
    # Handle save command
    if command == "save":
        state["save"] = True 
        print(f"[STATE] Command: SAVE")
        return False # Not a filter/state change
    # Handle filter toggle commands
    if command == "mustache":
        state["mustache"] = not state["mustache"]
    elif command == "glasses":
        state["glasses"] = not state["glasses"]
    elif command == "pimp_hat":
        state["pimp_hat"] = not state["pimp_hat"]
        state["cowboy_hat"] = False
    elif command == "cowboy_hat":
        state["cowboy_hat"] = not state["cowboy_hat"]
        state["pimp_hat"] = False
    elif command == "off":
        state["mustache"] = False
        state["glasses"] = False
        state["pimp_hat"] = False
        state["cowboy_hat"] = False
    else:
        return False # Unknown command
    # Print command received
    print(f"[{GREEN}INFO{ENDC}] Command: {command.upper()}")
    return True

async def recv_commands(websocket):
    """Loop to continuously receive commands from the client."""
    try:
        while True:
            msg = await websocket.recv()
            data = json.loads(msg)
            # Process command and check if filters changed
            if await process_command(websocket, data):
                print(f"--> [{BLUE}STATE{ENDC}] Updated:", state)
                await send_current_state(websocket)
                
    except (ConnectionClosedOK, ConnectionClosedError):
        print(f"[{GREEN}INFO{ENDC}] Client disconnected (receiver).")
        
# --- WebSocket handler ---
async def handler(websocket):
    global state
    print(f"[{GREEN}INFO{ENDC}] Client connected.")
    # Send initial state to sync UI on connection
    await send_current_state(websocket)
    # Start receiving commands using the external function
    asyncio.create_task(recv_commands(websocket))
    # Start video processing and sending frames
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            # Process frame
            mirrored = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            # Apply filters 
            if results.multi_face_landmarks:
                for fl in results.multi_face_landmarks:
                    if state["mustache"]:
                        image = apply_filter(fl, image, scale=1.2, offset_y=40,
                                             filter=stache, left_idx=234, right_idx=454, anchor_idx=1)
                    if state["glasses"]:
                        image = apply_filter(fl, image, scale=2.0, offset_y=20,
                                             filter=glasses, left_idx=33, right_idx=263, anchor_idx=168)
                    if state["pimp_hat"] and not state["cowboy_hat"]:
                        image = apply_filter(fl, image, scale=1.8, offset_y=-90,
                                             filter=pimp_hat, left_idx=234, right_idx=454, anchor_idx=10)
                    if state["cowboy_hat"] and not state["pimp_hat"]:
                        image = apply_filter(fl, image, scale=2.3, offset_y=-70,
                                             filter=cowboy_hat, left_idx=234, right_idx=454, anchor_idx=10)
            # Save image if requested
            if state["save"]:
                save_image(image)
                state["save"] = False
            # Encode frame
            _, buffer = cv2.imencode('.jpg', image)
            frame_data = base64.b64encode(buffer).decode('utf-8')
            # Send frame data to client
            await websocket.send(frame_data)
            await asyncio.sleep(frame_rate["60 fps"])
    except (ConnectionClosedOK, ConnectionClosedError):
        print(f"[{GREEN}INFO{ENDC}] Client disconnected (sender).")

# --- Main loop ---
async def main():
    print(f"[{GREEN}INFO{ENDC}] Starting WebSocket server on ws://localhost:{PORT}")
    server = await websockets.serve(handler, HOST, PORT)
    html_index_path = os.path.join(TEMPLATE_DIR, "index.html")
    # Print the instruction for the user
    print(f"\n[{GREEN}INFO{ENDC}] To open the Photobooth, open the following path in your browser:")
    print(f"[{GREEN}INFO{ENDC}] {html_index_path.replace('\\', '/')}\n")
    
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        print(f"[{GREEN}INFO{ENDC}] Server stopping...")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        server.close()
        await server.wait_closed()
        print(f"[{GREEN}INFO{ENDC}] Shutdown complete.")

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print(f"\n[{GREEN}INFO{ENDC}] Keyboard interrupt detected, shutting down...")
