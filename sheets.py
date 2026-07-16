import os
import gspread

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "authorized_user.json")


def is_configured() -> bool:
    return os.path.exists(CREDENTIALS_FILE)


def _get_worksheet(spreadsheet_id: str):
    gc = gspread.oauth(
        credentials_filename=CREDENTIALS_FILE,
        authorized_user_filename=TOKEN_FILE,
    )
    return gc.open_by_key(spreadsheet_id).get_worksheet(0)


def get_row_for_next_article(spreadsheet_id: str) -> dict:
    """D列が空の最初の行と A・B 列の値を返す。"""
    ws = _get_worksheet(spreadsheet_id)
    all_values = ws.get_all_values()
    for i, row in enumerate(all_values, 1):
        d_val = row[3] if len(row) > 3 else ""
        if not str(d_val).strip():
            return {
                "row":      i,
                "topic":    row[0] if len(row) > 0 else "",
                "keywords": row[1] if len(row) > 1 else "",
            }
    return {"row": len(all_values) + 1, "topic": "", "keywords": ""}


def write_to_d_column(row: int, content: str, spreadsheet_id: str) -> None:
    ws = _get_worksheet(spreadsheet_id)
    ws.update([[content]], f"D{row}", value_input_option="RAW")
