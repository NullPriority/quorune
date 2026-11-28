"""Load the reviewed Unfinity paper name-sticker slot data."""
import json
from pathlib import Path

_rows = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
NAME_STICKER_SHEETS = {sheet: (row[0], tuple(row[1])) for sheet, row in _rows.items()}
