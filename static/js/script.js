// Sử dụng các biến được định nghĩa trong index.html
const COMMAND_URL = window.COMMAND_URL;
const IMAGE_LIST_URL = window.IMAGE_LIST_URL;
const IMAGE_SERVE_BASE_URL = window.IMAGE_SERVE_BASE_URL;

// --- URL MỚI CHO SELF-TIMER ---
const SET_TEXT_URL = "/command/set_text/"; // Đường dẫn tới route mới
const CLEAR_TEXT_URL = "/command/clear_text"; // Đường dẫn tới route mới

// ... (Các hàm hideMessage, showMessage, updateUI, sendCommand, và logic Gallery giữ nguyên) ...
let local_state = {};
let messageTimeout;

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

        if (cmd === 'save') {
            if (data.status === 'saved') {
                showMessage("Photo saved successfully!", 4000);
                await getAndRenderImages();
            } else if (data.status === 'error') {
                showMessage("Error saving photo: " + data.message, 5000);
            }
        }
        
    } catch (error) {
        console.error("Error communicating with server:", error);
        showMessage("Connection Error: Could not reach the server or process command.", 0);
    }
}

let images = [];
let currentIndex = 0;

function renderCarousel() {
    const carouselContent = document.getElementById("carousel-content");
    carouselContent.innerHTML = ''; 
    if (images.length === 0) {
        carouselContent.innerHTML = '<p class="text-gray-400 text-lg">No photos saved yet.</p>';
        return;
    }
    images.forEach((filename, i) => {
        const imgEl = document.createElement("img");
        imgEl.src = IMAGE_SERVE_BASE_URL + filename; 
        imgEl.alt = "Saved Photo " + (i + 1);
        if (i === 0) {
            imgEl.classList.add("active");
        }
        carouselContent.appendChild(imgEl);
    });
    currentIndex = 0; 
}

function updateImage(dir) {
    const imgs = document.querySelectorAll("#carousel img");
    if (imgs.length === 0) return;
    imgs[currentIndex].classList.remove("active");
    currentIndex = (currentIndex + dir + imgs.length) % imgs.length;
    imgs[currentIndex].classList.add("active");
}

async function getAndRenderImages() {
    try {
        const response = await fetch(IMAGE_LIST_URL);
        if (!response.ok) {
            throw new Error('Failed to fetch image list.');
        }
        const data = await response.json();
        images = data.latest_images || [];
        renderCarousel();
    } catch (error) {
        console.error("Error loading images:", error);
    }
}

// --- LOGIC MỚI CHO SELF-TIMER ---

/**
 * Hàm sleep không chặn (non-blocking)
 * @param {number} ms - Thời gian chờ (mili-giây)
 */
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Gửi lệnh lên server để vẽ chữ lên video
 * @param {string} text - Chữ cần hiển thị (ví dụ: "10", "9", "SMILE!")
 */
async function setOverlayText(text) {
    try {
        if (text === "") {
            await fetch(CLEAR_TEXT_URL);
        } else {
            // Mã hóa text để đảm bảo an toàn khi truyền qua URL
            await fetch(SET_TEXT_URL + encodeURIComponent(text));
        }
    } catch (error) {
        console.error("Failed to set overlay text:", error);
        showMessage("Connection Error: Could not set text.", 0);
    }
}

/**
 * Bật/tắt tất cả các nút bấm
 * @param {boolean} enabled - True để bật, False để tắt
*/
function toggleButtons(enabled) {
    // Lấy tất cả các nút có class 'filter-button'
    const buttons = document.querySelectorAll('.filter-button');
    buttons.forEach(btn => {
        btn.disabled = !enabled; // Đặt thuộc tính disabled
        if (enabled) {
            // Xóa class của Tailwind để nút hiện rõ
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        } else {
            // Thêm class của Tailwind để làm mờ nút
            btn.classList.add('opacity-50', 'cursor-not-allowed');
        }
    });
}

/**
 * Hàm logic chính cho phiên chụp 4 tấm
 */
async function startPhotoSession() {
    // 1. Vô hiệu hóa tất cả các nút
    // toggleButtons(false); 
    const button = document.getElementById("btnStartSession");
    button.disabled = true; 
    button.classList.add('opacity-50', 'cursor-not-allowed');
    showMessage("Starting session... Get ready!", 2000);
    await sleep(1000); // Chờ 2 giây

    const totalPhotos = 4;
    for (let i = 1; i <= totalPhotos; i++) {
        showMessage(`Photo ${i} of ${totalPhotos}. Countdown...`, 1500);
        await sleep(1000); // Chờ 1.5 giây

        // 2. Bắt đầu đếm ngược 10 giây
        for (let count = 10; count > 0; count--) {
            await setOverlayText(String(count));
            await sleep(1000); // Chờ 1 giây
        }

        // 3. Chụp ảnh
        await setOverlayText("SMILE!"); 
        await sleep(500); // Hiển thị chữ "SMILE!" trong 0.5 giây
        await sendCommand('save'); 
        
        await setOverlayText(""); // Xóa chữ trên màn hình

        // Chờ một chút trước khi chụp tấm tiếp theo
        if (i < totalPhotos) {
            showMessage(`Get ready for next photo...`, 2000);
            await sleep(2000);
        }
    }

    // 4. Kết thúc phiên
    showMessage("Session finished! 4 photos saved.", 5000);
    button.disabled = false; 
    button.classList.remove('opacity-50', 'cursor-not-allowed');

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
    
    // Fetch and render saved images
    await getAndRenderImages();
    
    // Kích hoạt các nút khi tải trang (MỚI)
    toggleButtons(true);
}

// Attach event listeners to buttons
document.getElementById("btnMustache").onclick = () => sendCommand("mustache");
document.getElementById("btnGlasses").onclick = () => sendCommand("glasses");
document.getElementById("btnPimpHat").onclick = () => sendCommand("pimp_hat");
document.getElementById("btnCowboyHat").onclick = () => sendCommand("cowboy_hat");
document.getElementById("btnOff").onclick = () => sendCommand("off");
// document.getElementById("btnSave").onclick = () => sendCommand("save");
document.getElementById("btnStartSession").onclick = startPhotoSession;

// Start initialization
window.onload = initializeState;