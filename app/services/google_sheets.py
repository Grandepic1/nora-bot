import os

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
    ]

class GoogleSheetsService:
    def __init__(self):
        credentials_path = os.getenv("GOOGLE_SERVICE_CRED_PATH")

        if not credentials_path:
            raise RuntimeError(
            "GOOGLE_SERVICE_CRED_PATH is not configured"
            )

        credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=SCOPES
        )

        self.service = build(
            "sheets",
            "v4",
            credentials=credentials
        )