import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.tools.sheets import (
    append_rows,
    clear_row,
    clear_sheet,
    create_sheet,
    delete_row,
    delete_sheet,
    list_sheets,
    read_row,
    read_sheet,
    rename_sheet,
    update_row,
)

load_dotenv()

TOOLS = [
    list_sheets,
    read_sheet,
    read_row,
    append_rows,
    update_row,
    clear_row,
    delete_row,
    create_sheet,
    rename_sheet,
    clear_sheet,
    delete_sheet,
]

class GeminiService:
    def __init__(self):
        api_key = os.getenv("GEMINI_KEY")

        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        self.client = genai.Client(api_key=api_key)

    async def generate(self, message: str) -> str | None:
        response = await self.client.aio.models.generate_content(
            model=os.environ["GEMINI_MODEL"],
            contents=message,
            config=types.GenerateContentConfig(
                tools=TOOLS
            )
        )

        return response.text

    async def close(self):
        await self.client.aio.aclose()