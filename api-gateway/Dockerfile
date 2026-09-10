FROM python:3.11-slim-bookworm

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Slim CPU image: the gateway only serves FastAPI, proxies HTTP to Ollama,
# and runs TF-IDF in scikit-learn — no GPU work happens here, so no CUDA
# base and no NVIDIA runtime are needed (Ollama keeps the GPU).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir "numpy<2" && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
