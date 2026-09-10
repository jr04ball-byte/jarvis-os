# AI System Improvement Guide

## Current Architecture Overview

Your AI system has 3 main components:

1. **Ollama** - Runs local LLMs (Llama, Gemma, etc.)
2. **API Gateway** - FastAPI service that provides OpenAI-compatible API
3. **Open WebUI** - Web interface for chatting with models

## Key Code Files

### 1. API Gateway (`api-gateway/main.py`)
The heart of your system - handles all AI requests.

### 2. Python Client (`client.py`)
Easy-to-use Python wrapper for the API.

### 3. Docker Compose (`docker-compose.yml`)
Orchestrates all services.

---

## 10 Ways to Make Your AI System Better

### 1. **Add Streaming Responses** (Real-time output like ChatGPT)

Currently responses come all at once. Add streaming for better UX:

**Update `api-gateway/main.py`** - Add streaming support:

```python
from fastapi.responses import StreamingResponse
import json

@app.post("/v1/chat/completions")
async def chat_completion(request: ChatRequest):
    if request.stream:
        return StreamingResponse(
            stream_chat(request),
            media_type="text/event-stream"
        )
    # ... existing non-streaming code

async def stream_chat(request: ChatRequest):
    """Stream tokens as they're generated"""
    async with httpx.AsyncClient(timeout=300.0) as client:
        ollama_request = {
            "model": request.model,
            "messages": [{"role": msg.role, "content": msg.content} for msg in request.messages],
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens or 1024
            }
        }
        
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json=ollama_request
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    if "message" in data:
                        chunk = {
                            "choices": [{
                                "delta": {"content": data["message"].get("content", "")},
                                "index": 0
                            }]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
```

### 2. **Add Model Management API**

Let users download/delete models through the API:

```python
@app.post("/v1/models/pull")
async def pull_model(model: str):
    """Download a new model"""
    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": model, "stream": False}
        )
        return response.json()

@app.delete("/v1/models/{model_name}")
async def delete_model(model_name: str):
    """Remove a model to free up space"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            f"{OLLAMA_URL}/api/delete",
            json={"name": model_name}
        )
        return {"status": "deleted", "model": model_name}
```

### 3. **Add Conversation Memory**

Store chat history in SQLite so conversations persist:

```python
import sqlite3
from datetime import datetime

# Add to main.py
class ConversationDB:
    def __init__(self, db_path="conversations.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        self.conn.commit()
    
    def create_conversation(self, title: str, model: str):
        cursor = self.conn.execute(
            "INSERT INTO conversations (title, model) VALUES (?, ?)",
            (title, model)
        )
        self.conn.commit()
        return cursor.lastrowid
    
    def add_message(self, conv_id: int, role: str, content: str):
        self.conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conv_id, role, content)
        )
        self.conn.commit()
    
    def get_conversation(self, conv_id: int):
        cursor = self.conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,)
        )
        return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]

# Initialize
db = ConversationDB()

# Add endpoints
@app.post("/v1/conversations")
async def create_conversation(title: str, model: str):
    conv_id = db.create_conversation(title, model)
    return {"conversation_id": conv_id}

@app.get("/v1/conversations/{conv_id}")
async def get_conversation(conv_id: int):
    messages = db.get_conversation(conv_id)
    return {"messages": messages}
```

### 4. **Add RAG (Retrieval Augmented Generation)**

Let the AI answer questions about your documents:

```python
# Install: pip install chromadb sentence-transformers
import chromadb
from sentence_transformers import SentenceTransformer

class DocumentRAG:
    def __init__(self):
        self.client = chromadb.Client()
        self.collection = self.client.create_collection("documents")
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
    
    def add_document(self, text: str, doc_id: str):
        """Add a document to the knowledge base"""
        embedding = self.embedder.encode(text).tolist()
        self.collection.add(
            embeddings=[embedding],
            documents=[text],
            ids=[doc_id]
        )
    
    def search(self, query: str, n_results: int = 3):
        """Search for relevant documents"""
        query_embedding = self.embedder.encode(query).tolist()
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results
        )
        return results['documents'][0]
    
    def augment_prompt(self, user_query: str):
        """Add relevant context to the prompt"""
        relevant_docs = self.search(user_query)
        context = "\n\n".join(relevant_docs)
        return f"Context:\n{context}\n\nQuestion: {user_query}"

# Add endpoints
rag = DocumentRAG()

@app.post("/v1/documents")
async def upload_document(doc_id: str, content: str):
    rag.add_document(content, doc_id)
    return {"status": "added", "doc_id": doc_id}

@app.post("/v1/chat/completions-rag")
async def chat_with_rag(request: ChatRequest):
    # Augment the last message with context
    last_message = request.messages[-1].content
    augmented = rag.augment_prompt(last_message)
    request.messages[-1].content = augmented
    
    # Use normal chat completion
    return await chat_completion(request)
```

### 5. **Add Function Calling / Tools**

Let the AI use tools (calculator, web search, etc.):

```python
import re
from typing import List, Dict, Any

class ToolRegistry:
    def __init__(self):
        self.tools = {}
    
    def register(self, name: str, description: str, func):
        self.tools[name] = {
            "description": description,
            "function": func
        }
    
    def get_tools_prompt(self):
        tools_text = "\n".join([
            f"- {name}: {info['description']}"
            for name, info in self.tools.items()
        ])
        return f"""You have access to these tools:
{tools_text}

To use a tool, respond with: TOOL[tool_name](argument)
Example: TOOL[calculate](2 + 2)
"""
    
    def execute(self, tool_name: str, argument: str):
        if tool_name in self.tools:
            return self.tools[tool_name]["function"](argument)
        return f"Tool {tool_name} not found"

# Define tools
tools = ToolRegistry()

def calculate(expr: str):
    try:
        return str(eval(expr))
    except:
        return "Invalid expression"

def get_weather(location: str):
    # Placeholder - integrate with real weather API
    return f"Weather in {location}: Sunny, 72°F"

tools.register("calculate", "Perform mathematical calculations", calculate)
tools.register("weather", "Get current weather for a location", get_weather)

# Add to chat completion
async def chat_with_tools(request: ChatRequest):
    # Add tools to system message
    system_msg = ChatMessage(role="system", content=tools.get_tools_prompt())
    request.messages.insert(0, system_msg)
    
    max_iterations = 5
    for i in range(max_iterations):
        response = await chat_completion(request)
        content = response["choices"][0]["message"]["content"]
        
        # Check if AI wants to use a tool
        tool_match = re.search(r'TOOL\[(\w+)\]\(([^)]+)\)', content)
        if tool_match:
            tool_name = tool_match.group(1)
            argument = tool_match.group(2)
            result = tools.execute(tool_name, argument)
            
            # Add result to conversation
            request.messages.append(ChatMessage(role="assistant", content=content))
            request.messages.append(ChatMessage(role="user", content=f"Tool result: {result}"))
        else:
            # No more tools needed, return final response
            return response
    
    return response
```

### 6. **Add Multiple Model Support with Auto-Routing**

Route requests to the best model for the task:

```python
class ModelRouter:
    def __init__(self):
        self.models = {
            "coding": "deepseek-coder:6.7b",
            "reasoning": "qwen2.5:14b",
            "general": "gemma2:9b",
            "fast": "llama3.1:8b"
        }
    
    def detect_task_type(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        
        if any(word in prompt_lower for word in ["code", "programming", "function", "debug"]):
            return "coding"
        elif any(word in prompt_lower for word in ["analyze", "reason", "explain", "compare"]):
            return "reasoning"
        elif any(word in prompt_lower for word in ["quick", "simple", "summarize"]):
            return "fast"
        else:
            return "general"
    
    def route(self, prompt: str, requested_model: str = None) -> str:
        if requested_model:
            return requested_model
        
        task_type = self.detect_task_type(prompt)
        return self.models.get(task_type, self.models["general"])

router = ModelRouter()

@app.post("/v1/chat/completions-smart")
async def smart_chat(request: ChatRequest):
    # Auto-select best model
    last_message = request.messages[-1].content
    best_model = router.route(last_message, request.model)
    request.model = best_model
    
    return await chat_completion(request)
```

### 7. **Add Prompt Templates Library**

Pre-built prompts for common tasks:

```python
PROMPT_TEMPLATES = {
    "summarize": """Summarize the following text in 3-5 bullet points:

{input}

Summary:""",
    
    "code_review": """Review this code and provide feedback:

{input}

Review:
- Correctness:
- Best Practices:
- Improvements:""",
    
    "explain_eli5": """Explain the following concept like I'm 5 years old:

{input}

Simple explanation:""",
    
    "translate": """Translate the following text to {language}:

{input}

Translation:""",
}

@app.post("/v1/prompt-template/{template_name}")
async def use_template(template_name: str, input_text: str, language: str = None):
    if template_name not in PROMPT_TEMPLATES:
        raise HTTPException(404, "Template not found")
    
    template = PROMPT_TEMPLATES[template_name]
    prompt = template.format(input=input_text, language=language or "")
    
    # Execute with model
    request = ChatRequest(
        model="gemma2:9b",
        messages=[ChatMessage(role="user", content=prompt)]
    )
    return await chat_completion(request)
```

### 8. **Add Performance Monitoring**

Track token usage, response times, costs:

```python
from collections import defaultdict
import time

class PerformanceMonitor:
    def __init__(self):
        self.stats = defaultdict(lambda: {
            "requests": 0,
            "tokens": 0,
            "total_time": 0.0
        })
    
    def record(self, model: str, tokens: int, duration: float):
        self.stats[model]["requests"] += 1
        self.stats[model]["tokens"] += tokens
        self.stats[model]["total_time"] += duration
    
    def get_stats(self):
        result = {}
        for model, data in self.stats.items():
            result[model] = {
                "requests": data["requests"],
                "total_tokens": data["tokens"],
                "avg_tokens_per_request": data["tokens"] / data["requests"] if data["requests"] > 0 else 0,
                "avg_time_seconds": data["total_time"] / data["requests"] if data["requests"] > 0 else 0,
                "tokens_per_second": data["tokens"] / data["total_time"] if data["total_time"] > 0 else 0
            }
        return result

monitor = PerformanceMonitor()

# Update chat_completion to track stats
@app.post("/v1/chat/completions")
async def chat_completion(request: ChatRequest):
    start_time = time.time()
    
    # ... existing code ...
    response = # get response
    
    duration = time.time() - start_time
    tokens = response["usage"]["total_tokens"]
    monitor.record(request.model, tokens, duration)
    
    return response

@app.get("/v1/performance")
async def get_performance():
    return monitor.get_stats()
```

### 9. **Add Model Comparison Mode**

Run same prompt on multiple models and compare:

```python
@app.post("/v1/compare")
async def compare_models(
    prompt: str,
    models: List[str] = ["gemma2:9b", "llama3.1:8b", "qwen2.5:7b"]
):
    results = {}
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        for model in models:
            start = time.time()
            
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 1024}
                }
            )
            
            duration = time.time() - start
            result = response.json()
            
            results[model] = {
                "response": result["message"]["content"],
                "tokens": result.get("eval_count", 0),
                "time_seconds": duration,
                "tokens_per_second": result.get("eval_count", 0) / duration
            }
    
    return results
```

### 10. **Add Voice Input/Output**

Make it conversational with speech:

```python
# Install: pip install openai-whisper TTS
import whisper
from TTS.api import TTS

class VoiceAI:
    def __init__(self):
        self.whisper = whisper.load_model("base")
        self.tts = TTS("tts_models/en/ljspeech/tacotron2-DDC")
    
    def transcribe_audio(self, audio_path: str) -> str:
        result = self.whisper.transcribe(audio_path)
        return result["text"]
    
    def text_to_speech(self, text: str, output_path: str):
        self.tts.tts_to_file(text=text, file_path=output_path)

voice = VoiceAI()

@app.post("/v1/voice/transcribe")
async def transcribe(audio_file: UploadFile):
    # Save uploaded audio
    audio_path = f"/tmp/{audio_file.filename}"
    with open(audio_path, "wb") as f:
        f.write(await audio_file.read())
    
    # Transcribe
    text = voice.transcribe_audio(audio_path)
    return {"text": text}

@app.post("/v1/voice/speak")
async def speak(text: str):
    output_path = "/tmp/speech.wav"
    voice.text_to_speech(text, output_path)
    return FileResponse(output_path, media_type="audio/wav")
```

---

## Quick Wins (Easy Improvements)

### 1. Add CORS for Web Apps
Already done, but make sure it's configured correctly in production.

### 2. Add API Keys (Security)

```python
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader

API_KEYS = {"your-secret-key"}  # Load from environment
api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key not in API_KEYS:
        raise HTTPException(403, "Invalid API key")
    return api_key

# Add to endpoints
@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
```

### 3. Add Rate Limiting

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/v1/chat/completions")
@limiter.limit("10/minute")
async def chat_completion(request: Request, chat_request: ChatRequest):
    # ... existing code
```

### 4. Add Request/Response Logging

```python
import logging

logging.basicConfig(
    filename="api_requests.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    
    logging.info(f"{request.method} {request.url.path} - {response.status_code} - {duration:.2f}s")
    return response
```

---

## Testing Your Improvements

Create `test_improvements.py`:

```python
import requests

BASE_URL = "http://localhost:8000"

def test_streaming():
    response = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model": "gemma2:9b",
            "messages": [{"role": "user", "content": "Count to 10"}],
            "stream": True
        },
        stream=True
    )
    
    for line in response.iter_lines():
        if line:
            print(line.decode())

def test_comparison():
    response = requests.post(
        f"{BASE_URL}/v1/compare",
        json={
            "prompt": "What is the meaning of life?",
            "models": ["gemma2:9b", "llama3.1:8b"]
        }
    )
    
    for model, result in response.json().items():
        print(f"\n{model}:")
        print(f"  Response: {result['response'][:100]}...")
        print(f"  Speed: {result['tokens_per_second']:.1f} tokens/s")

if __name__ == "__main__":
    print("Testing streaming...")
    test_streaming()
    
    print("\n\nTesting model comparison...")
    test_comparison()
```

---

## Next Steps

1. Pick 2-3 improvements that solve your biggest pain points
2. Implement them one at a time
3. Test thoroughly before moving to the next
4. Update your `docker-compose.yml` if adding new dependencies
5. Document your changes in the README

The most impactful improvements for most users:
1. **Streaming** - Better UX
2. **RAG** - Answer questions about your documents
3. **Conversation Memory** - Persistent chat history
4. **Model Routing** - Automatically pick the best model

Start with those and build from there!
