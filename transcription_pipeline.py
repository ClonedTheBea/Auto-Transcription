from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_AUDIO = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
RAW_AUDIO = {".aac", ".aiff", ".aif", ".flac", ".ogg", ".opus", ".wma", ".mov", ".mkv"}


@dataclass(frozen=True)
class Paths:
    root: Path
    todo: Path
    transcribing: Path
    qc: Path
    finished: Path
    needs_attention: Path
    archive: Path


@dataclass(frozen=True)
class Settings:
    openai_model: str
    diarization_model: str
    speaker_labels: bool
    language: str | None
    prompt: str | None
    poll_seconds: int
    archive_finished: bool
    zip_finished: bool
    max_upload_mb: int
    log_path: Path
    spreadsheet_path: Path
    paths: Paths


def load_settings(config_path: Path) -> Settings:
    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            import tomli as tomllib

        with config_path.open("rb") as handle:
            config = tomllib.load(handle)

    pipeline = config.get("pipeline", {})
    openai = config.get("openai", {})
    spreadsheet = config.get("spreadsheet", {})

    orders_dir = Path(pipeline.get("orders_dir", "Orders"))
    if not orders_dir.is_absolute():
        orders_dir = config_path.parent / orders_dir

    paths = Paths(
        root=orders_dir,
        todo=orders_dir / "1 - To Do",
        transcribing=orders_dir / "2 - Transcribing",
        qc=orders_dir / "3 - Quality Control",
        finished=orders_dir / "4 - Finished",
        needs_attention=orders_dir / "X - Needs Attention",
        archive=orders_dir / "Z - Archived Orders",
    )

    log_path = Path(pipeline.get("log_path", "logs/pipeline.log"))
    if not log_path.is_absolute():
        log_path = config_path.parent / log_path

    spreadsheet_path = Path(spreadsheet.get("path", "orders.csv"))
    if not spreadsheet_path.is_absolute():
        spreadsheet_path = config_path.parent / spreadsheet_path

    return Settings(
        openai_model=openai.get("model", "gpt-4o-transcribe"),
        diarization_model=openai.get("diarization_model", "gpt-4o-transcribe-diarize"),
        speaker_labels=bool(openai.get("speaker_labels", False)),
        language=openai.get("language") or None,
        prompt=openai.get("prompt") or None,
        poll_seconds=int(pipeline.get("poll_seconds", 30)),
        archive_finished=bool(pipeline.get("archive_finished", True)),
        zip_finished=bool(pipeline.get("zip_finished", True)),
        max_upload_mb=int(pipeline.get("max_upload_mb", 24)),
        log_path=log_path,
        spreadsheet_path=spreadsheet_path,
        paths=paths,
    )


def ensure_layout(settings: Settings) -> None:
    for path in (
        settings.paths.todo,
        settings.paths.transcribing,
        settings.paths.qc,
        settings.paths.finished,
        settings.paths.needs_attention,
        settings.paths.archive,
    ):
        path.mkdir(parents=True, exist_ok=True)
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)


def log(settings: Settings, message: str, level: str = "INFO") -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} [{level}] {message}"
    with settings.log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    try:
        print(line, flush=True)
    except OSError:
        pass


def next_order(todo: Path) -> Path | None:
    orders = [path for path in todo.iterdir() if path.is_dir()]
    return sorted(orders, key=lambda item: item.stat().st_mtime)[0] if orders else None


def unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return destination.with_name(f"{destination.name}-{stamp}")


def move_order(order: Path, stage: Path) -> Path:
    destination = unique_destination(stage / order.name)
    return Path(shutil.move(str(order), str(destination)))


def read_metadata(order_dir: Path) -> dict[str, Any]:
    metadata_path = order_dir / "order.json"
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return infer_metadata(order_dir)


def infer_metadata(order_dir: Path) -> dict[str, Any]:
    parts = order_dir.name.split(" - ")
    order_number = parts[0].strip()
    return {
        "order_number": order_number,
        "amount_paid": "",
        "currency": "",
        "service": "transcription",
        "buyer": "",
        "source": "manual",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def write_metadata(order_dir: Path, metadata: dict[str, Any]) -> None:
    metadata["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with (order_dir / "order.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")


def find_audio(order_dir: Path) -> Path:
    candidates = [
        path
        for path in order_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO.union(RAW_AUDIO)
    ]
    if not candidates:
        raise FileNotFoundError(f"No audio file found in {order_dir}")
    return sorted(candidates, key=lambda item: item.stat().st_size, reverse=True)[0]


def prepare_audio(source: Path, order_dir: Path, max_upload_mb: int) -> Path:
    upload_limit = max_upload_mb * 1024 * 1024
    if source.suffix.lower() in SUPPORTED_AUDIO and source.stat().st_size <= upload_limit:
        return source

    converted_dir = order_dir / "converted"
    converted_dir.mkdir(exist_ok=True)
    output = converted_dir / f"{source.stem}.mp3"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "64k",
        str(output),
    ]
    subprocess.run(command, check=True)
    if output.stat().st_size > upload_limit:
        raise ValueError(
            f"Converted audio is still larger than {max_upload_mb} MB. "
            "Split the audio or lower the bitrate before sending it to the API."
        )
    return output


def transcribe(audio_path: Path, settings: Settings) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI()
    request: dict[str, Any] = {
        "model": settings.diarization_model if settings.speaker_labels else settings.openai_model,
        "file": audio_path.open("rb"),
    }
    if settings.language:
        request["language"] = settings.language
    if settings.speaker_labels:
        request["response_format"] = "diarized_json"
        request["chunking_strategy"] = "auto"
    elif settings.prompt:
        request["prompt"] = settings.prompt

    try:
        result = client.audio.transcriptions.create(**request)
    finally:
        request["file"].close()

    return transcription_to_dict(result)


def transcription_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {"text": str(result)}


def speaker_name(label: str, speaker_map: dict[str, str]) -> str:
    if label not in speaker_map:
        speaker_map[label] = f"Speaker {len(speaker_map) + 1}"
    return speaker_map[label]


def format_transcript(transcription: dict[str, Any]) -> str:
    segments = transcription.get("segments") or []
    if not segments:
        return str(transcription.get("text", "")).strip()

    speaker_map: dict[str, str] = {}
    lines: list[str] = []
    current_speaker = ""
    current_text: list[str] = []

    for segment in segments:
        label = str(segment.get("speaker", "unknown"))
        speaker = speaker_name(label, speaker_map)
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if speaker != current_speaker and current_text:
            lines.append(f"{current_speaker}: {' '.join(current_text)}")
            current_text = []
        current_speaker = speaker
        current_text.append(text)

    if current_text:
        lines.append(f"{current_speaker}: {' '.join(current_text)}")

    return "\n\n".join(lines).strip()


def write_transcript(order_dir: Path, transcription: dict[str, Any], metadata: dict[str, Any]) -> None:
    transcript_dir = order_dir / "transcripts"
    transcript_dir.mkdir(exist_ok=True)
    title = metadata.get("order_number") or order_dir.name
    text = format_transcript(transcription) + "\n"
    (transcript_dir / "transcript.txt").write_text(text, encoding="utf-8")
    (transcript_dir / "transcript.md").write_text(f"# Transcript {title}\n\n{text}", encoding="utf-8")
    with (transcript_dir / "transcription.raw.json").open("w", encoding="utf-8") as handle:
        json.dump(transcription, handle, indent=2)
        handle.write("\n")


def append_spreadsheet(path: Path, metadata: dict[str, Any], status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "order_number",
        "buyer",
        "service",
        "amount_paid",
        "currency",
        "status",
        "folder",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "order_number": metadata.get("order_number", ""),
                "buyer": metadata.get("buyer", ""),
                "service": metadata.get("service", "transcription"),
                "amount_paid": metadata.get("amount_paid", ""),
                "currency": metadata.get("currency", ""),
                "status": status,
                "folder": metadata.get("folder", ""),
            }
        )


def process_one(settings: Settings) -> bool:
    order = next_order(settings.paths.todo)
    if order is None:
        return False

    working = move_order(order, settings.paths.transcribing)
    log(settings, f"Started order: {working.name}")
    try:
        metadata = read_metadata(working)
        metadata["folder"] = str(working)
        metadata["status"] = "transcribing"
        write_metadata(working, metadata)

        audio = find_audio(working)
        prepared = prepare_audio(audio, working, settings.max_upload_mb)
        transcript = transcribe(prepared, settings)
        write_transcript(working, transcript, metadata)

        metadata["status"] = "quality_control"
        qc_dir = move_order(working, settings.paths.qc)
        metadata["folder"] = str(qc_dir)
        write_metadata(qc_dir, metadata)
        append_spreadsheet(settings.spreadsheet_path, metadata, "quality_control")
        log(settings, f"Moved order to Quality Control: {qc_dir.name}")
    except Exception as exc:
        fail_order(settings, working, exc)
    return True


def fail_order(settings: Settings, order_dir: Path, exc: Exception) -> None:
    message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    (order_dir / "error.txt").write_text(message, encoding="utf-8")
    try:
        metadata = read_metadata(order_dir)
    except Exception:
        metadata = infer_metadata(order_dir)
    metadata["status"] = "needs_attention"
    metadata["folder"] = str(order_dir)
    write_metadata(order_dir, metadata)
    failed = move_order(order_dir, settings.paths.needs_attention)
    metadata["folder"] = str(failed)
    write_metadata(failed, metadata)
    append_spreadsheet(settings.spreadsheet_path, metadata, "needs_attention")
    log(settings, f"Moved order to Needs Attention: {failed.name}. Error: {exc}", "ERROR")


def zip_folder(folder: Path) -> Path:
    zip_path = folder.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent))
    return zip_path


def archive_finished(settings: Settings) -> int:
    count = 0
    for order in sorted(path for path in settings.paths.finished.iterdir() if path.is_dir()):
        metadata = read_metadata(order)
        finished_at = datetime.now()
        if settings.zip_finished:
            zip_folder(order)
        archive_dir = (
            settings.paths.archive
            / f"{finished_at:%Y}"
            / f"{finished_at:%m}"
            / f"{finished_at:%d}"
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = move_order(order, archive_dir)
        metadata["status"] = "archived"
        metadata["folder"] = str(archived)
        write_metadata(archived, metadata)
        append_spreadsheet(settings.spreadsheet_path, metadata, "archived")
        count += 1
        log(settings, f"Archived finished order: {archived.name}")
    return count


def watch(settings: Settings) -> None:
    ensure_layout(settings)
    log(settings, f"Watcher started. Polling every {settings.poll_seconds} seconds.")
    while True:
        processed = False
        try:
            processed = process_one(settings)
        except Exception as exc:
            log(settings, f"Unhandled processing error: {exc}", "ERROR")
            log(settings, traceback.format_exc(), "ERROR")
        try:
            if settings.archive_finished:
                archive_finished(settings)
        except Exception as exc:
            log(settings, f"Archive error: {exc}", "ERROR")
            log(settings, traceback.format_exc(), "ERROR")
        if not processed:
            time.sleep(settings.poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Automate the local Fiverr transcription pipeline.")
    parser.add_argument("--config", default="config.toml", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the Orders folder layout.")
    subparsers.add_parser("process-one", help="Process the oldest order in Orders/1 - To Do.")
    subparsers.add_parser("watch", help="Continuously process To Do orders and archive Finished orders.")
    subparsers.add_parser("archive-finished", help="Archive manually finished orders.")
    args = parser.parse_args()

    settings = load_settings(args.config)
    ensure_layout(settings)

    if args.command == "init":
        print(f"Order folders ready under {settings.paths.root}")
    elif args.command == "process-one":
        did_work = process_one(settings)
        print("Processed one order." if did_work else "No To Do orders found.")
    elif args.command == "archive-finished":
        count = archive_finished(settings)
        print(f"Archived {count} finished order(s).")
    elif args.command == "watch":
        watch(settings)
    else:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
