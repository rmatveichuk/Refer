import logging
import base64
import requests
from pathlib import Path
from PyQt6.QtCore import QSettings

logger = logging.getLogger(__name__)

class LlmClient:
    """Client for interacting with local LLMs/VLMs via OpenAI-compatible API (e.g. LM Studio)."""
    
    def __init__(self):
        self.settings = QSettings("ReferApp", "ReferSettings")
        # По умолчанию LM Studio использует этот URL
        self.default_url = "http://localhost:1234/v1"
        self.default_model = "local-model"
        self.default_prompt = "You are an expert architect and interior designer. Describe this image in detail: style, materials, lighting, colors, and key elements. Keep it concise."
        
    @property
    def api_url(self) -> str:
        url = self.settings.value("lm_studio_url", self.default_url)
        return url.rstrip('/')

    @property
    def model_name(self) -> str:
        return self.settings.value("lm_studio_model", self.default_model)

    @property
    def system_prompt(self) -> str:
        return self.settings.value("lm_studio_prompt", self.default_prompt)
        
    def _encode_image(self, image_path: str) -> str:
        """Encodes an image to base64."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to the API. Returns (success, message)."""
        try:
            # Query the /models endpoint
            response = requests.get(f"{self.api_url}/models", timeout=5)
            response.raise_for_status()
            
            data = response.json()
            models = [m.get("id", "Unknown") for m in data.get("data", [])]
            if not models:
                return True, "Подключение успешно, но модели не найдены."
                
            models_str = ", ".join(models)
            return True, f"Подключение успешно! Доступные модели: {models_str}"
        except requests.exceptions.RequestException as e:
            logger.error(f"LM Studio connection failed: {e}")
            return False, f"Ошибка подключения: {str(e)}"
            
    def analyze_image(self, image_path: str) -> str:
        """Sends an image to the VLM and returns the description."""
        if not Path(image_path).exists():
            return "Ошибка: Файл не найден."
            
        base64_image = self._encode_image(image_path)
        
        headers = {
            "Content-Type": "application/json"
        }
        
        # Simplified payload for better compatibility with various local models
        # Some local VLM servers don't like 'system' role or separate user messages
        combined_prompt = f"{self.system_prompt}\n\nPlease analyze this image."
        
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": combined_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.2
        }
        
        logger.info(f"Sending request to LM Studio ({self.api_url}). Model: {self.model_name}")
        
        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=300 # Image analysis can take time
            )
            response.raise_for_status()
            
            data = response.json()
            message = data["choices"][0]["message"]
            
            # Support for reasoning models (like DeepSeek R1 or Qwen-VL-Reasoning)
            # They often put the text in 'reasoning_content' or 'content'
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            
            # Combine or pick the non-empty one
            final_text = content if content else reasoning
            return final_text.strip()
        except requests.exceptions.RequestException as e:
            logger.error(f"LM Studio API request failed: {e}")
            return f"Ошибка API: {str(e)}"
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected API response format: {e}")
            return "Ошибка: Неожиданный ответ от сервера."
