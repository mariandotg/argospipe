import csv
import json

from pydantic import ValidationError

from argospipe.config import FileSourceConfig
from argospipe.sources.base import RawJob

_RAW_JOB_FIELDS = frozenset(RawJob.model_fields)
_REQUIRED_FIELDS = ("title", "company", "url")


class FileSource:
    name: str = "file"
    config: FileSourceConfig

    def __init__(self, config: FileSourceConfig) -> None:
        self.config = config
        self.errors: list[str] = []

    async def fetch(self) -> list[RawJob]:
        self.errors.clear()
        extension = self.config.path.suffix.lower()

        if extension == ".csv":
            records = self._read_csv()
        elif extension == ".json":
            records = self._read_json()
        else:
            raise ValueError(
                f"Unsupported file source extension {extension or '(none)'!r}; "
                "expected .csv or .json"
            )

        jobs = []
        for row_number, record in enumerate(records, start=1):
            job = self._parse_record(record, row_number)
            if job is not None:
                jobs.append(job)
        return jobs

    def _read_csv(self) -> list[object]:
        records: list[object] = []
        with self.config.path.open(encoding="utf-8-sig", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                record: dict[str, object] = {}
                for key, value in row.items():
                    if key is not None:
                        record[key] = value
                records.append(record)
        return records

    def _read_json(self) -> list[object]:
        content = self.config.path.read_text(encoding="utf-8")
        if not content.strip():
            return []

        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("JSON file source must contain a list of objects")
        return data

    def _parse_record(self, record: object, row_number: int) -> RawJob | None:
        if not isinstance(record, dict):
            self.errors.append(f"row {row_number}: expected an object")
            return None

        missing = [
            field
            for field in _REQUIRED_FIELDS
            if record.get(field) is None
            or (isinstance(record.get(field), str) and not record[field].strip())
        ]
        if missing:
            self.errors.append(f"row {row_number}: missing required field(s): {', '.join(missing)}")
            return None

        values = {field: record[field] for field in _RAW_JOB_FIELDS if field in record}
        if not values.get("source"):
            values["source"] = "file"
        if not values.get("external_id"):
            values["external_id"] = values["url"]

        try:
            return RawJob.model_validate(values)
        except ValidationError as error:
            self.errors.append(f"row {row_number}: invalid job: {error}")
            return None
