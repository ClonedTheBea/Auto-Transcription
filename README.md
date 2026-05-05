# Auto-Transcription

A local server-side pipeline for Fiverr transcription orders.

## What Can Be Automated

The safest first version automates everything after an order folder exists on the server:

1. Put a new order folder in `Orders/1 - To Do`.
2. The worker moves it to `Orders/2 - Transcribing`.
3. Audio is converted with `ffmpeg` if needed.
4. The OpenAI transcription API creates `transcripts/transcript.txt` and `transcripts/transcript.md`.
5. The folder moves to `Orders/3 - Quality Control`.
6. You manually review and move it to `Orders/4 - Finished`.
7. The worker can zip and archive finished orders into `Orders/Z - Archived Orders/YYYY/MM/DD`.
8. Basic order data is appended to `orders.csv`.

Fiverr order intake and delivery should only be added through an approved route, such as an official/partner integration, an email/Zapier bridge, or a manual handoff. Avoid scraping or unattended account automation unless Fiverr explicitly allows it for your account.

## Setup

```powershell
python -m pip install -r requirements.txt
Copy-Item config.example.toml config.toml
$env:OPENAI_API_KEY = "sk-..."
python transcription_pipeline.py init
```

OpenAI API data is not used for model training by default. See OpenAI's business data page: <https://openai.com/business-data>.

## Order Folder Format

Create a folder like:

```text
Orders/
  1 - To Do/
    FO123456789 - Client Name/
      audio-file.mp3
      order.json
```

Example `order.json`:

```json
{
  "order_number": "FO123456789",
  "amount_paid": "25.00",
  "currency": "USD",
  "service": "transcription",
  "buyer": "client-name",
  "source": "manual"
}
```

If `order.json` is missing, the script will infer the order number from the folder name and leave the money fields blank.

## Speaker Labels

To identify different speakers, edit `config.toml`:

```toml
[openai]
model = "gpt-4o-transcribe"
diarization_model = "gpt-4o-transcribe-diarize"
speaker_labels = true
language = ""
prompt = ""
```

When enabled, the script requests diarized JSON from OpenAI and formats the transcript as:

```text
Speaker 1: Hello, thanks for joining.

Speaker 2: No problem, happy to help.
```

The raw API response is also saved at `transcripts/transcription.raw.json`.

## Commands

Process the oldest To Do order:

```powershell
python transcription_pipeline.py process-one
```

Run continuously:

```powershell
python transcription_pipeline.py watch
```

Archive anything you manually moved into Finished:

```powershell
python transcription_pipeline.py archive-finished
```

## Continuous Watching

Start the watcher:

```powershell
.\Start-Watcher.cmd
```

Keep that window open while you want the watcher running. It checks `Orders/1 - To Do` repeatedly, processes new orders one at a time, and also archives anything you manually move into `Orders/4 - Finished`.

Check whether it is running:

```powershell
.\Status-Watcher.cmd
```

Stop it:

```powershell
.\Stop-Watcher.cmd
```

You can also stop it by pressing `Ctrl+C` in the watcher window.

Logs are written to:

```text
logs/pipeline.log
logs/watcher.out.log
logs/watcher.err.log
```

If an order fails, the watcher writes `error.txt` inside the order folder and moves it to:

```text
Orders/X - Needs Attention
```

## Notes

- Current OpenAI transcription models include `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`, and `whisper-1`.
- Speaker labels use `gpt-4o-transcribe-diarize` with `response_format = "diarized_json"` and automatic chunking.
- The OpenAI transcription endpoint supports common audio formats including `mp3`, `mp4`, `mpeg`, `mpga`, `m4a`, `wav`, and `webm`.
- The public docs list a 25 MB upload limit for transcription files, so the default config keeps converted audio under 24 MB.
- Delivery back to Fiverr is deliberately not automated in this first pass because that depends on Fiverr account permissions and policy.
