"""Single-host durable storage and worker for large document jobs."""

from __future__ import annotations

import base64
import csv
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from pydantic import TypeAdapter

from redaction_mcp.core.models.document import Document
from redaction_mcp.core.models.errors import RedactionError, ReplacementError
from redaction_mcp.core.models.jobs import JobInfo, JobSubmit, ResultChunk, UploadCreate, UploadInfo
from redaction_mcp.core.models.redaction import RedactionOptions, RedactionTarget
from redaction_mcp.core.models.replacement import ReplacementTarget
from redaction_mcp.core.services.redaction_service import redact_document
from redaction_mcp.core.services.replacement_service import replace_document
from redaction_mcp.core.services.mime_detection import (
    CSV_MIME_TYPE,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    PPTM_MIME_TYPE,
    PPTX_MIME_TYPE,
    XLSX_MIME_TYPE,
    detect_mime_type,
)
from redaction_mcp.core.engines.pdf_redaction import redact_pdf_document
from redaction_mcp.core.engines.pdf_replacement import replace_pdf_document
from redaction_mcp.core.engines.docx_redaction import redact_docx_document
from redaction_mcp.core.engines.docx_replacement import replace_docx_document
from redaction_mcp.core.engines.pptx_redaction import redact_pptx_document
from redaction_mcp.core.engines.pptx_replacement import replace_pptx_document
from redaction_mcp.core.engines.spreadsheet import redact_xlsx_document, replace_xlsx_document
from redaction_mcp.core.engines.spreadsheet import _redact_value, _replace_value
from redaction_mcp.core.services.replacement_text import ReplacementContext
from redaction_mcp.core.services.limits import (
    ARCHIVE_EXPANDED_LIMIT,
    CHUNK_SIZE,
    DIRECT_LIMIT,
    LARGE_LIMIT,
    OFFICE_EXTENSIONS,
    OFFICE_LIMIT,
)


class JobConflictError(ValueError):
    pass


class JobNotFoundError(ValueError):
    pass


class JobManager:
    def __init__(self, storage_path: Path, *, workers: int = 1, retention_seconds: int = 86_400) -> None:
        self.storage_path = storage_path
        self.upload_path = storage_path / "uploads"
        self.result_path = storage_path / "results"
        self.database_path = storage_path / "jobs.sqlite3"
        self.workers = max(1, workers)
        self.retention_seconds = retention_seconds
        self._condition = threading.Condition()
        self._started = False
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.upload_path.mkdir(exist_ok=True)
        self.result_path.mkdir(exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, mime_type TEXT,
                    size_bytes INTEGER NOT NULL, received_bytes INTEGER NOT NULL DEFAULT 0,
                    next_chunk INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, upload_id TEXT NOT NULL, operation TEXT NOT NULL,
                    targets_json TEXT NOT NULL, options_json TEXT, status TEXT NOT NULL,
                    filename TEXT, mime_type TEXT, output_size INTEGER, error TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                """
            )
            now = time.time()
            connection.execute(
                "UPDATE jobs SET status='failed', error='Processing interrupted by server restart', updated_at=? WHERE status='running'",
                (now,),
            )

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._started = True
            for index in range(self.workers):
                worker = threading.Thread(target=self._worker, name=f"redaction-job-{index}", daemon=True)
                self._threads.append(worker)
                worker.start()
            self._condition.notify_all()

    def stop(self) -> None:
        self._stopped.set()
        with self._condition:
            self._condition.notify_all()
        for worker in self._threads:
            worker.join(timeout=2)

    def create_upload(self, request: UploadCreate) -> UploadInfo:
        suffix = Path(request.filename).suffix.lower()
        office_mimes = {DOCX_MIME_TYPE, PPTX_MIME_TYPE, PPTM_MIME_TYPE, XLSX_MIME_TYPE}
        if suffix in OFFICE_EXTENSIONS or request.mime_type in office_mimes:
            limit = OFFICE_LIMIT
        elif suffix in {".pdf", ".csv"} or request.mime_type in {PDF_MIME_TYPE, CSV_MIME_TYPE}:
            limit = LARGE_LIMIT
        else:
            limit = DIRECT_LIMIT
        if request.size_bytes > limit:
            raise ValueError(f"{suffix or 'File'} uploads are limited to {limit} bytes")
        upload_id = uuid4().hex
        created_at = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO uploads VALUES (?, ?, ?, ?, 0, 0, ?)",
                (upload_id, Path(request.filename).name, request.mime_type, request.size_bytes, created_at),
            )
        self._upload_file(upload_id).touch(mode=0o600)
        return UploadInfo(
            upload_id=upload_id,
            filename=Path(request.filename).name,
            mime_type=request.mime_type,
            size_bytes=request.size_bytes,
            received_bytes=0,
            next_chunk=0,
        )

    def upload_chunk(self, upload_id: str, index: int, chunk: bytes) -> UploadInfo:
        if not chunk or len(chunk) > CHUNK_SIZE:
            raise ValueError(f"Chunks must contain between 1 and {CHUNK_SIZE} bytes")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None:
                raise JobNotFoundError("Upload not found")
            if index != row["next_chunk"]:
                raise JobConflictError(f"Expected chunk index {row['next_chunk']}")
            received = row["received_bytes"] + len(chunk)
            if received > row["size_bytes"]:
                raise ValueError("Chunk exceeds the declared upload size")
            with self._upload_file(upload_id).open("ab") as output:
                output.write(chunk)
            connection.execute(
                "UPDATE uploads SET received_bytes=?, next_chunk=? WHERE id=?",
                (received, index + 1, upload_id),
            )
            return UploadInfo(
                upload_id=upload_id,
                filename=row["filename"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                received_bytes=received,
                next_chunk=index + 1,
            )

    def submit_job(self, request: JobSubmit) -> JobInfo:
        with self._connect() as connection:
            upload = connection.execute("SELECT * FROM uploads WHERE id=?", (request.upload_id,)).fetchone()
            if upload is None:
                raise JobNotFoundError("Upload not found")
            if upload["received_bytes"] != upload["size_bytes"]:
                raise JobConflictError("Upload is incomplete")
            self._validate_archive(self._upload_file(request.upload_id), upload["filename"], upload["mime_type"])
            if request.operation == "redact":
                TypeAdapter(list[RedactionTarget]).validate_python(request.targets)
            else:
                TypeAdapter(list[ReplacementTarget]).validate_python(request.targets)
                if request.options is not None:
                    raise ValueError("options are valid only for redaction jobs")
            job_id = uuid4().hex
            now = time.time()
            connection.execute(
                "INSERT INTO jobs (id, upload_id, operation, targets_json, options_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    job_id,
                    request.upload_id,
                    request.operation,
                    json.dumps(request.targets),
                    request.options.model_dump_json() if request.options else None,
                    now,
                    now,
                ),
            )
        self.start()
        with self._condition:
            self._condition.notify()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobInfo:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError("Job not found")
        return self._job_info(row)

    def result_chunk(self, job_id: str, offset: int, limit: int) -> ResultChunk:
        if offset < 0 or not 1 <= limit <= CHUNK_SIZE:
            raise ValueError(f"offset must be nonnegative and limit must be between 1 and {CHUNK_SIZE}")
        job = self.get_job(job_id)
        if job.status != "succeeded" or job.output_size is None:
            raise JobConflictError("Job result is not available")
        if offset > job.output_size:
            raise ValueError("offset exceeds the result size")
        with self._result_file(job_id).open("rb") as source:
            source.seek(offset)
            data = source.read(limit)
        next_offset = offset + len(data)
        return ResultChunk(
            base64data=base64.b64encode(data).decode("ascii"),
            offset=offset,
            next_offset=next_offset,
            total_size=job.output_size,
            eof=next_offset >= job.output_size,
        )

    def result_bytes(self, job_id: str, offset: int, limit: int) -> tuple[bytes, JobInfo]:
        chunk = self.result_chunk(job_id, offset, limit)
        return base64.b64decode(chunk.base64data), self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status == "running":
            raise JobConflictError("Running jobs cannot be deleted")
        with self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            remaining = connection.execute("SELECT 1 FROM jobs WHERE upload_id=?", (job.upload_id,)).fetchone()
            if remaining is None:
                connection.execute("DELETE FROM uploads WHERE id=?", (job.upload_id,))
        self._result_file(job_id).unlink(missing_ok=True)
        if remaining is None:
            self._upload_file(job.upload_id).unlink(missing_ok=True)

    def cleanup(self) -> int:
        cutoff = time.time() - self.retention_seconds
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status IN ('succeeded', 'failed') AND updated_at < ?", (cutoff,)
            ).fetchall()
        for row in rows:
            self.delete_job(row["id"])
        return len(rows)

    def _worker(self) -> None:
        while not self._stopped.is_set():
            self.cleanup()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "UPDATE jobs SET status='running', updated_at=? WHERE id=?", (time.time(), row["id"])
                    )
            if row is None:
                with self._condition:
                    self._condition.wait(timeout=5)
                continue
            self._process(row)

    def _process(self, row: sqlite3.Row) -> None:
        try:
            with self._connect() as connection:
                upload = connection.execute("SELECT * FROM uploads WHERE id=?", (row["upload_id"],)).fetchone()
            source_path = self._upload_file(row["upload_id"])
            result_path = self._result_file(row["id"])
            mime_type = upload["mime_type"] or detect_mime_type(filename=upload["filename"])
            if mime_type == PDF_MIME_TYPE:
                document = Document(base64data="", filename=upload["filename"], mime_type=mime_type)
                if row["operation"] == "redact":
                    targets = TypeAdapter(list[RedactionTarget]).validate_json(row["targets_json"])
                    options = RedactionOptions.model_validate_json(row["options_json"]) if row["options_json"] else RedactionOptions()
                    result = redact_pdf_document(
                        document, targets, options, source_path=source_path, output_path=result_path
                    )
                else:
                    targets = TypeAdapter(list[ReplacementTarget]).validate_json(row["targets_json"])
                    result = replace_pdf_document(
                        document, targets, source_path=source_path, output_path=result_path
                    )
            elif mime_type == CSV_MIME_TYPE:
                filename = self._process_csv(row, source_path, result_path)
                result = Document(base64data="", filename=filename, mime_type=CSV_MIME_TYPE)
            else:
                data = source_path.read_bytes()
                document = Document(
                    base64data="" if mime_type in {DOCX_MIME_TYPE, PPTX_MIME_TYPE, PPTM_MIME_TYPE, XLSX_MIME_TYPE}
                    else base64.b64encode(data).decode("ascii"),
                    filename=upload["filename"],
                    mime_type=mime_type,
                )
                if row["operation"] == "redact":
                    targets = TypeAdapter(list[RedactionTarget]).validate_json(row["targets_json"])
                    options = RedactionOptions.model_validate_json(row["options_json"]) if row["options_json"] else RedactionOptions()
                    if mime_type == DOCX_MIME_TYPE:
                        result = redact_docx_document(document, targets, options, data=data)
                    elif mime_type in {PPTX_MIME_TYPE, PPTM_MIME_TYPE}:
                        result = redact_pptx_document(document, targets, options, data=data)
                    elif mime_type == XLSX_MIME_TYPE:
                        result = redact_xlsx_document(document, targets, options, data=data)
                    else:
                        result = redact_document(document, targets, options)
                else:
                    targets = TypeAdapter(list[ReplacementTarget]).validate_json(row["targets_json"])
                    if mime_type == DOCX_MIME_TYPE:
                        result = replace_docx_document(document, targets, data=data)
                    elif mime_type in {PPTX_MIME_TYPE, PPTM_MIME_TYPE}:
                        result = replace_pptx_document(document, targets, data=data)
                    elif mime_type == XLSX_MIME_TYPE:
                        result = replace_xlsx_document(document, targets, data=data)
                    else:
                        result = replace_document(document, targets)
            if isinstance(result, (RedactionError, ReplacementError)):
                raise ValueError(result.message)
            if not result_path.exists():
                result_path.write_bytes(base64.b64decode(result.base64data))
            output_size = result_path.stat().st_size
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status='succeeded', filename=?, mime_type=?, output_size=?, updated_at=? WHERE id=?",
                    (result.filename, result.mime_type, output_size, time.time(), row["id"]),
                )
        except Exception as exc:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                    (str(exc), time.time(), row["id"]),
                )

    def _process_csv(self, row: sqlite3.Row, source_path: Path, result_path: Path) -> str:
        with source_path.open("rb") as source_binary:
            bom = source_binary.read(3) == b"\xef\xbb\xbf"
        encoding = "utf-8-sig" if bom else "utf-8"
        with source_path.open("r", encoding=encoding, newline="") as source:
            sample = source.read(8192)
            source.seek(0)
            newline = "\r\n" if "\r\n" in sample else "\n"
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            with result_path.open("w", encoding=encoding, newline="") as output:
                writer = csv.writer(output, dialect=dialect, lineterminator=newline)
                if row["operation"] == "redact":
                    targets = TypeAdapter(list[RedactionTarget]).validate_json(row["targets_json"])
                    options = RedactionOptions.model_validate_json(row["options_json"]) if row["options_json"] else RedactionOptions()

                    def transform(value: str) -> str:
                        for target in targets:
                            value = _redact_value(value, target, options)
                        return value
                else:
                    replacement_targets = TypeAdapter(list[ReplacementTarget]).validate_json(row["targets_json"])
                    context = ReplacementContext()

                    def transform(value: str) -> str:
                        for target in replacement_targets:
                            value = _replace_value(value, target, context)
                        return value
                for values in csv.reader(source, dialect=dialect):
                    writer.writerow([transform(value) for value in values])
        stem = Path(self._upload_filename(row["upload_id"])).stem
        return f"{stem}-{'redacted' if row['operation'] == 'redact' else 'replaced'}.csv"

    def _upload_filename(self, upload_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT filename FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if row is None:
            raise JobNotFoundError("Upload not found")
        return row["filename"]

    def _validate_archive(self, path: Path, filename: str, mime_type: str | None = None) -> None:
        if Path(filename).suffix.lower() not in OFFICE_EXTENSIONS and mime_type not in {
            DOCX_MIME_TYPE, PPTX_MIME_TYPE, PPTM_MIME_TYPE, XLSX_MIME_TYPE,
        }:
            return
        try:
            with ZipFile(path) as archive:
                expanded = sum(entry.file_size for entry in archive.infolist())
                compressed = sum(max(entry.compress_size, 1) for entry in archive.infolist())
        except BadZipFile as exc:
            raise ValueError("Office upload is not a valid ZIP package") from exc
        if expanded > ARCHIVE_EXPANDED_LIMIT or expanded > compressed * 100:
            raise ValueError("Office package expands beyond the allowed safety limit")

    def _upload_file(self, upload_id: str) -> Path:
        return self.upload_path / f"{upload_id}.bin"

    def _result_file(self, job_id: str) -> Path:
        return self.result_path / f"{job_id}.bin"

    @staticmethod
    def _job_info(row: Any) -> JobInfo:
        return JobInfo(
            job_id=row["id"], upload_id=row["upload_id"], operation=row["operation"], status=row["status"],
            filename=row["filename"], mime_type=row["mime_type"], output_size=row["output_size"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )


_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_job_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            storage = Path(os.getenv("REDACTION_STORAGE_PATH", Path.home() / ".redaction-mcp"))
            workers = int(os.getenv("REDACTION_JOB_WORKERS", "1"))
            retention = int(os.getenv("REDACTION_JOB_RETENTION_SECONDS", "86400"))
            _manager = JobManager(storage, workers=workers, retention_seconds=retention)
            _manager.start()
        return _manager


def reset_job_manager() -> None:
    """Forget the process-global manager; intended for isolated tests."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
        _manager = None
