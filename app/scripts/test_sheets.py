from app.services.google_sheets import GoogleSheetsService


sheets = GoogleSheetsService()

SPREADSHEET_ID = sheets.extract_spreadsheet_id(
    "https://docs.google.com/spreadsheets/d/15KT6GxL953UTShmilWFgQ9D-dS5qiN_-gWasqlxddC4/edit?usp=sharing"
)

print("=== Spreadsheet info ===")
print(sheets.get_spreadsheet_info(SPREADSHEET_ID))


print("=== Sheets ===")
print(sheets.list_sheets(SPREADSHEET_ID))

result = sheets.append_rows(
    SPREADSHEET_ID,
    "Sheet1",
    [
        ["No", "Nama", "Nominal", "Makanan"],
        [1, "Budi", 100000, "Ayam Geprek"],
    ],
)

print(result)