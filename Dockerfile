# Sử dụng base image Python 3.10-slim
FROM python:3.10-slim

# Cài đặt các thư viện hệ thống cần thiết cho OpenCV
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Đặt thư mục làm việc bên trong container
WORKDIR /app

# Sao chép file requirements trước để tận dụng cache
COPY requirements.txt .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép mã nguồn của ứng dụng
COPY server.py .
COPY filters ./filters
COPY templates ./templates

# Tạo thư mục để lưu ảnh (sẽ được mount từ máy host)
RUN mkdir saved_image

# Mở port 8765 mà server WebSocket đang chạy
EXPOSE 8765

# Lệnh để chạy ứng dụng khi container khởi động
CMD ["python", "server.py"]