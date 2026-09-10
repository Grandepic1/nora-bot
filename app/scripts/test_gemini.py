from app.services.gemini import GeminiService
from app.services.google_sheets import GoogleSheetsService


gemini = GeminiService()
SPREADSHEET_ID = GoogleSheetsService().get_spreadsheet_id(
    "https://docs.google.com/spreadsheets/d/15KT6GxL953UTShmilWFgQ9D-dS5qiN_-gWasqlxddC4/edit?usp=sharing"
)
response = gemini.generate(f"""
        Sheet ID: {SPREADSHEET_ID}
        Yang odading ternyata salah bukan 2 juta tapi 20 ribu. Tolong update
    """
    )

print(response)