"""CSV upload adapter.

The pragmatic path for devices with no network integration at all: almost every
attendance machine can export a punch log to CSV or Excel, and an operator can
upload that file. It is fully implemented and tested, and needs no connectivity
to the device.

Each row becomes a payload dictionary keyed by column name, which the shared
field mapping then interprets - so a device whose columns are
``EmpCode,PunchTime,InOut`` is configured rather than coded for.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.modules.devices.adapters.base import (
    AdapterDescription,
    ConfigField,
    register,
)
from app.modules.devices.policy import DeviceDataError

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000


class CsvUploadAdapter:
    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_type="csv_upload",
            name="CSV upload",
            mode="push",
            maturity="tested",
            summary=(
                "Upload a punch log exported from the device. Works with any machine "
                "that can produce CSV, and needs no network access to the device."
            ),
            config_fields=(
                ConfigField(
                    key="delimiter",
                    label="Column delimiter",
                    help_text="Defaults to a comma. Use ';' or a tab if your export differs.",
                    example=",",
                ),
                ConfigField(
                    key="skip_rows",
                    label="Rows to skip before the header",
                    help_text="Some exports begin with a title or a blank line.",
                    example="0",
                ),
                ConfigField(
                    key="field_mapping.employee_ref",
                    label="Employee column",
                    required=True,
                    help_text="The column holding the device's identifier for the person.",
                    example="EmpCode",
                ),
                ConfigField(
                    key="field_mapping.timestamp",
                    label="Timestamp column",
                    required=True,
                    example="PunchTime",
                ),
                ConfigField(
                    key="field_mapping.timestamp_format",
                    label="Timestamp format",
                    required=True,
                    help_text=(
                        "Stated explicitly, never guessed: 03/04/2026 is a different "
                        "day under day-first and month-first readings."
                    ),
                    example="dmy_hms_slash",
                ),
                ConfigField(
                    key="field_mapping.direction",
                    label="Direction column",
                    help_text="Leave empty if the export does not say in or out.",
                    example="InOut",
                ),
            ),
            caveats=(
                "An export containing the same punches as a previous upload is "
                "deduplicated, so re-uploading an overlapping file is safe.",
                "Rows that cannot be read are stored and reported individually; one "
                "bad row does not discard the file.",
            ),
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        delimiter = config.get("delimiter", ",")
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise DeviceDataError(
                "The delimiter must be a single character.", field_name="delimiter"
            )
        skip_rows = config.get("skip_rows", 0)
        if not isinstance(skip_rows, int) or not 0 <= skip_rows <= 50:
            raise DeviceDataError(
                "skip_rows must be a whole number between 0 and 50.",
                field_name="skip_rows",
            )

    def extract(
        self, *, config: dict[str, Any], body: bytes, content_type: str | None
    ) -> list[dict[str, Any]]:
        if len(body) > MAX_BYTES:
            raise DeviceDataError(
                f"The file is larger than {MAX_BYTES // 1024 // 1024} MB.",
                field_name="file",
            )

        text = _decode(body)
        delimiter = config.get("delimiter", ",")
        skip_rows = int(config.get("skip_rows", 0))

        stream = io.StringIO(text)
        for _ in range(skip_rows):
            stream.readline()

        reader = csv.DictReader(stream, delimiter=delimiter)
        if reader.fieldnames is None:
            raise DeviceDataError("The file has no header row.", field_name="file")

        # Preserve the operator's column names verbatim: the field mapping is
        # written against what the export actually says.
        payloads: list[dict[str, Any]] = []
        for index, row in enumerate(reader):
            if index >= MAX_ROWS:
                raise DeviceDataError(
                    f"The file has more than {MAX_ROWS} rows. Split it into smaller exports.",
                    field_name="file",
                )
            cleaned = {
                (key or "").strip(): (value.strip() if isinstance(value, str) else value)
                for key, value in row.items()
                if key is not None
            }
            if any(value not in (None, "") for value in cleaned.values()):
                cleaned["_row"] = index + 1 + skip_rows + 1
                payloads.append(cleaned)

        return payloads


def _decode(content: bytes) -> str:
    # Device exports are frequently Windows-encoded, and Excel writes UTF-8 with
    # a BOM; utf-8-sig strips it so the first column is not read as
    # "﻿EmpCode".
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DeviceDataError(
        "The file is not readable as text. Export it as CSV UTF-8.", field_name="file"
    )


register(CsvUploadAdapter())
