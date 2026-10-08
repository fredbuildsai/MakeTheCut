import csv
import json
import re
from datetime import datetime
from pathlib import Path
from config import APPLICATIONS_DIR, CV_PATH, PREFERENCES_PATH
from models import ApplicationRecord, QARecord

_STORE = APPLICATIONS_DIR / "analyses.json"
_SCHEMA = APPLICATIONS_DIR / "analyses.schema.json"
_QA_STORE = APPLICATIONS_DIR / "qa_log.json"
_CONVERSATION_LOG = APPLICATIONS_DIR / "conversations.csv"
_CONVERSATION_HEADERS = ["timestamp", "user_id", "username", "chat_id", "message_id", "text"]


def load_cv() -> str:
    if not CV_PATH.exists():
        raise FileNotFoundError(
            f"CV not found at '{CV_PATH}'. Please create profile/cv.md with your CV."
        )
    return CV_PATH.read_text(encoding="utf-8")


def load_preferences() -> str:
    if not PREFERENCES_PATH.exists():
        raise FileNotFoundError(
            f"Preferences not found at '{PREFERENCES_PATH}'. "
            "Please create profile/preferences.md with your job search preferences."
        )
    return PREFERENCES_PATH.read_text(encoding="utf-8")


def _sanitize(name: str, max_len: int = 40) -> str:
    """Make a string safe for folder/file names."""
    name = re.sub(r"[^\w\s-]", "", name.strip())
    name = re.sub(r"\s+", "_", name)
    return name[:max_len] or "Unknown"


def create_application_folder(company: str, role: str) -> Path:
    """Create and return a YYYY-MM-DD_Company_Role folder inside applications/."""
    date = datetime.now().strftime("%Y-%m-%d")
    folder = APPLICATIONS_DIR / f"{date}_{_sanitize(company)}_{_sanitize(role)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_file(folder: Path, filename: str, content: str) -> Path:
    path = folder / filename
    path.write_text(content, encoding="utf-8")
    return path


def log_conversation(user_id: int, username: str, chat_id: int, message_id: int, text: str) -> None:
    """Append a message row to conversations.csv."""
    write_header = not _CONVERSATION_LOG.exists()
    with _CONVERSATION_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CONVERSATION_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "username": username or "",
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        })


def append_qa(record: QARecord) -> None:
    """Append a Q&A record to qa_log.json."""
    records: list[dict] = []
    if _QA_STORE.exists():
        try:
            records = json.loads(_QA_STORE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            records = []
    records.append(json.loads(record.model_dump_json()))
    _QA_STORE.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def load_records() -> list[ApplicationRecord]:
    """Return all saved ApplicationRecords, newest first."""
    if not _STORE.exists():
        return []
    try:
        raw = json.loads(_STORE.read_text(encoding="utf-8"))
        return [ApplicationRecord.model_validate(r) for r in reversed(raw)]
    except (json.JSONDecodeError, ValueError):
        return []


def append_record(record: ApplicationRecord) -> None:
    """Upsert a validated ApplicationRecord into analyses.json (matched by id)."""
    records: list[dict] = []
    if _STORE.exists():
        try:
            records = json.loads(_STORE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            records = []

    record_dict = json.loads(record.model_dump_json())
    for i, existing in enumerate(records):
        if existing.get("id") == record_dict["id"]:
            records[i] = record_dict
            break
    else:
        records.append(record_dict)

    _STORE.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    # Keep the schema reference in sync with the current models on every write.
    write_schema()


def write_schema() -> None:
    """Write the current ApplicationRecord JSON schema, overwriting any stale copy."""
    schema = ApplicationRecord.model_json_schema()
    _SCHEMA.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
