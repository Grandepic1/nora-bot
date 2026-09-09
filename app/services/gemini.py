import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

class GeminiService:
    def __init__(self):
        api_key = os.getenv("GEMINI_KEY")

        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        self.client = genai.Client(api_key=api_key)

    def generate(self, message: str) -> str:
        response = self.client.models.generate_content(
            model=os.getenv("GEMINI_MODEL"),
            contents=message
        )

        return response.text