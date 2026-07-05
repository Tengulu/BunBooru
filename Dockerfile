FROM python:3.12-slim

# Install ffmpeg and libmagic for video thumbnails and file type detection
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libmagic1 \
    unrar-free \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create storage directories in case volume isn't mounted yet
RUN mkdir -p /app/storage/originals /app/storage/thumbs

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
