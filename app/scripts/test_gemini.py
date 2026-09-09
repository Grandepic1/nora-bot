from app.services.gemini import GeminiService


gemini = GeminiService()

response = gemini.generate("Reply exactly with: Gemini {your model} is connected")

print(response)