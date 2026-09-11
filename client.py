import requests
import json

class LocalAI:
    """
    Python client for your self-hosted AI system
    Drop-in replacement for OpenAI's client
    """

    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.chat_url = f"{base_url}/v1/chat/completions"
        self.models_url = f"{base_url}/v1/models"
        self.health_url = f"{base_url}/health"
        self.stats_url = f"{base_url}/stats"

    def chat(self, model, messages, temperature=0.7, max_tokens=1024, stream=False, conversation_id=None, use_rag=False):
        """
        Send a chat completion request

        Args:
            model: Model name (e.g., "llama3.1:8b")
            messages: List of {"role": "user/assistant", "content": "..."}
            temperature: 0.0-1.0, higher = more creative
            max_tokens: Maximum response length (default: 1024)
            stream: Stream response (not yet implemented)

        Returns:
            Response content as string
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            "conversation_id": conversation_id,
            "use_rag": use_rag
        }

        try:
            response = requests.post(self.chat_url, json=payload, timeout=300)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            return f"Error: {str(e)}"

    def list_models(self):
        """Get list of available models"""
        try:
            response = requests.get(self.models_url, timeout=10)
            response.raise_for_status()
            result = response.json()
            return [model["id"] for model in result.get("data", [])]
        except requests.exceptions.RequestException as e:
            return f"Error: {str(e)}"

    def health(self):
        """Check system health"""
        try:
            response = requests.get(self.health_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return f"Error: {str(e)}"

    def stats(self):
        """Get system statistics"""
        try:
            response = requests.get(self.stats_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return f"Error: {str(e)}"


def main():
    """Example usage"""

    # Initialize client
    ai = LocalAI()

    # Check health
    print("Checking system health...")
    health = ai.health()
    print(json.dumps(health, indent=2))
    print()

    # List available models
    print("Available models:")
    models = ai.list_models()
    if isinstance(models, list):
        for model in models:
            print(f"  - {model}")
    else:
        print(models)
    print()

    # Chat with the AI
    if isinstance(models, list) and len(models) > 0:
        model = models[0]
        print(f"Chatting with {model}...")

        response = ai.chat(
            model=model,
            messages=[
                {"role": "user", "content": "Explain what you are in one sentence."}
            ],
            temperature=0.7
        )

        print(f"Response: {response}")
    else:
        print("No models available. Please download a model first:")
        print("  docker exec -it ollama ollama pull llama3.1:8b")


if __name__ == "__main__":
    main()

