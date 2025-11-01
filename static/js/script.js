// Sử dụng các biến được định nghĩa trong index.html
const COMMAND_URL = window.COMMAND_URL;
const IMAGE_LIST_URL = window.IMAGE_LIST_URL;
const IMAGE_SERVE_BASE_URL = window.IMAGE_SERVE_BASE_URL;
const BATCH_UPLOAD_URL = window.BATCH_UPLOAD_URL;

// --- LẤY CÁC THÀNH PHẦN DOM ---
const countdownOverlay = document.getElementById("countdownOverlay");
const videoFeed = document.getElementById("videoFeed");
const canvas = document.getElementById("captureCanvas");
const ctx = canvas.getContext("2d");
const sessionCapturesContainer = document.getElementById("session-captures");

const btnStartSession = document.getElementById("btnStartSession");
const btnContinue = document.getElementById("btnContinue");

let capturedImages = [];
let messageTimeout;
let isSessionRunning = false;

function hideMessage() {
    const box = document.getElementById('messageBox');
    box.classList.add('hidden');
    clearTimeout(messageTimeout);
}

function showMessage(msg, duration = 4000) {
    const box = document.getElementById('messageBox');
    const msgText = document.getElementById('messageText');
    clearTimeout(messageTimeout);
    msgText.textContent = msg;
    box.classList.remove('hidden');
    if (duration > 0) {
        messageTimeout = setTimeout(hideMessage, duration);
    }
}

function updateUI(state) {
    document.getElementById("btnMustache").classList.toggle("active", state.mustache);
    document.getElementById("btnGlasses").classList.toggle("active", state.glasses);
    document.getElementById("btnPimpHat").classList.toggle("active", state.pimp_hat);
    document.getElementById("btnCowboyHat").classList.toggle("active", state.cowboy_hat);
}

async function sendCommand(cmd) {
    try {
        const response = await fetch(COMMAND_URL + cmd); 
        if (!response.ok) {
            throw new Error(`Server responded with status: ${response.status}`);
        }
        const data = await response.json();
        
        if (data.state) {
            updateUI(data.state);
        }
        
    } catch (error) {
        console.error("Error communicating with server:", error);
        showMessage("Connection Error: Could not reach the server or process command.", 0);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function showCountdownText(text) {
    if (text === "") {
        countdownOverlay.style.display = "none";
    } else {
        countdownOverlay.textContent = text;
        countdownOverlay.style.display = "flex";
    }
}

function captureFrame() {
    if (videoFeed.naturalWidth === 0) {
        console.error("Video feed not loaded or has no dimensions.");
        return null;
    }
    canvas.width = videoFeed.naturalWidth;
    canvas.height = videoFeed.naturalHeight;
    
    ctx.drawImage(videoFeed, 0, 0, canvas.width, canvas.height);
    
    return canvas.toDataURL('image/jpeg', 0.9);
}

async function sendBatchUpload(imageArray) {
    showMessage("Saving 4 photos to server... Please wait.", 0);
    try {
        const response = await fetch(BATCH_UPLOAD_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ images: imageArray }),
        });

        if (!response.ok) {
            throw new Error(`Server responded with status: ${response.status}`);
        }

        const data = await response.json();
        
        if (data.status === 'saved') {
            showMessage(`Successfully saved ${data.count} photos!`, 4000);
        } else {
            showMessage("Error saving photos: " + data.message, 5000);
        }

    } catch (error) {
        console.error("Error batch uploading photos:", error);
        showMessage("Connection Error: Could not save photos.", 0);
    }
}
async function setupStartSession() {
    isSessionRunning = false;
    btnStartSession.innerHTML = "Start 4-Photo Session";
    btnStartSession.classList.remove('bg-red-600', 'hover:bg-red-700');
    btnStartSession.classList.add('bg-green-600', 'hover:bg-green-700');
}
async function setupStopSession() {
    isSessionRunning = true;
    btnStartSession.innerHTML = "Stop Photo Session!";
    btnStartSession.classList.add('bg-red-600', 'hover:bg-red-700');
    btnStartSession.classList.remove('bg-green-600', 'hover:bg-green-700');
}
async function togglePhotoSession() {
    if (isSessionRunning) {
        await setupStartSession();
        capturedImages = [];
        showCountdownText("");
        sessionCapturesContainer.innerHTML = "";
    } else {
        await setupStopSession();
        await startPhotoSession();
    }
}
async function startPhotoSession() {
    //Ẩn continue
    btnContinue.style.display = "none";

    // Xóa ảnh của phiên trước
    sessionCapturesContainer.innerHTML = ''; 
    capturedImages = [];
    showMessage("Starting session... Get ready!", 2000);
    await sleep(1000);

    if (!isSessionRunning) { return; }
    
    const totalPhotos = 2;
    for (let i = 1; i <= totalPhotos; i++) {
        showMessage(`Photo ${i} of ${totalPhotos}. Countdown...`, 1500);
        await sleep(1000); 
        if (!isSessionRunning) { break; }

        // 2. Bắt đầu đếm ngược 10 giây (FE)
        for (let count = 10; count > 0; count--) {
            showCountdownText(String(count));
            await sleep(1000); 
            if (!isSessionRunning) { break; }
        }

        if (!isSessionRunning) { break; }

        // 3. Chụp ảnh (FE)
        showCountdownText("SMILE!");
        await sleep(500);
        
        if (!isSessionRunning) { break; }

        const imageDataUrl = captureFrame();
        capturedImages.push(imageDataUrl);
        
        const imgElement = document.createElement('img');
        imgElement.src = imageDataUrl;
        imgElement.classList.add('rounded-lg', 'shadow-md', 'mt-4', 'mx-auto');
        sessionCapturesContainer.appendChild(imgElement);
        // ---------------------------------
        
        showCountdownText("");

        if (i < totalPhotos) {
            showMessage(`Get ready for next photo...`, 2000);
            await sleep(2000);
        }
        if (!isSessionRunning) { break; }
    }
    // 4. Hiện nút continue và cờ running
    if (!isSessionRunning) { return; }
    btnContinue.style.display = "block";
    await setupStartSession();
}

// --- Initialization ---
async function initializeState() {
    // Fetch initial filter state
    try {
        const response = await fetch('/get_state'); 
        const data = await response.json();
        if (data.state) {
            updateUI(data.state);
        }
    } catch (error) {
        console.error("Could not fetch initial state:", error);
        showMessage("Waiting for Flask server to start... Check your Python terminal for errors.", 0);
    }
}

// Attach event listeners to buttons
document.getElementById("btnMustache").onclick = () => sendCommand("mustache");
document.getElementById("btnGlasses").onclick = () => sendCommand("glasses");
document.getElementById("btnPimpHat").onclick = () => sendCommand("pimp_hat");
document.getElementById("btnCowboyHat").onclick = () => sendCommand("cowboy_hat");
document.getElementById("btnOff").onclick = () => sendCommand("off");
btnStartSession.onclick = () => togglePhotoSession();
btnContinue.onclick = () => sendBatchUpload(capturedImages)
// Start initialization
window.onload = initializeState;