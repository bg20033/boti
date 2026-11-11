# Boti 0.7 — Provoje këtë VOD

The first 0.7 phase adds a bounded, non-publishing trial through the existing transcription, selection, render, quality and single-repair pipeline. It does not replace the local models or the real editorial pilot.

## Using a trial

1. Select a campaign (optional), destination groups, and a completed Kick/Twitch/YouTube link or an inbox file in the usual form.
2. Click **Provoje këtë VOD**, enter the start as `HH:MM:SS`, choose 30 seconds to 15 minutes and a limit of 3–5 candidates.
3. The trial waits until live, unpaused agents collectively advertise transcription, analysis, render, quality and repair capabilities. Main FFmpeg/ffprobe and disk headroom are required. The report explains missing capabilities. A waiting trial does not block later normal jobs.
4. Open the trial in the jobs table to watch its report: elapsed time, tasks/errors, selected moments, original-source timestamps, selection reasons, quality history and playable outputs. A JSON report can be downloaded.

The clip limit is a ceiling, not a promise. A short interval can yield fewer clips or zero. Campaign duration/source/score constraints and automatic quality checks still apply; the score threshold is not lowered. Trial elapsed time includes waiting and technical retries, not just active GPU time.

## Source handling and restart

Remote trials request only the selected time range using yt-dlp's official range callback and FFmpeg. Sources that cannot return the selected duration fail; there is no automatic fallback to processing the whole VOD. Downloaded duration must match the requested range within one second. Provider/protocol behavior can still affect bytes transferred and seeking speed. A completed local sample and download checkpoint are reused after interruption; partial range downloads may need restarting according to the source/FFmpeg behavior.

Local trials cut the selected range without copying the whole inbox original. The full source hash is checked before and after cutting. Requests outside the source duration fail clearly. Source timestamps in the report add the trial offset to the actual selected/repaired clip times.

A trial's identity includes platform, video ID, start, duration and candidate limit. Repeating the same request returns the existing trial. A different interval or limit creates another trial. Production imports remain separate and keep their existing identities, campaign bindings and history. Migration defaults all existing jobs to `production`; it never activates a trial or publishing retroactively.

Original sample files follow the existing original-retention period. Final outputs follow output retention; report/version records remain. Deleting expired originals does not remove the original inbox file.

## Publishing and capacity

Trials always use automatic review, but never enqueue publications or automatically export a TikTok posting package. The publication gate independently rejects trial clips even if an outbox entry is present or someone approves the clip manually. Global publishing and account connection settings cannot override this. To run normal production, use the regular add-job action; there is no implicit conversion of trial clips into posts.

The CLI production-throughput count excludes trial clips. A trial helps check a specific interval; it is not evidence of 1,000 production clips per day, campaign acceptance or earnings.

## API

`POST /api/jobs` accepts the existing source, groups and campaign fields plus:

```json
{
  "url": "https://twitch.tv/videos/123",
  "group_ids": ["pc-1-g1"],
  "trial": {"start_seconds": 60, "duration_seconds": 300, "max_clips": 5}
}
```

Bounds: start 0–604800 seconds, duration 30–900 seconds, candidate limit 3–5. Omit `trial` for the existing production behavior. Trial review mode is always automatic.

Authenticated `GET /api/jobs/{id}/trial-report` returns readiness, status, range, counts, task errors, clip quality history, original-source coordinates and output links. It returns 404 for a production job. Reports and video links use the existing administrator authentication.

## Verification and remaining work

156 tests passed locally with full FFmpeg, including 18 new trial cases. They cover identity/migration, invalid options, readiness, range requests/reuse, wrong-duration and out-of-bounds responses, disk pressure, publication isolation, candidate limits, original retention and excluding trials from production throughput. One integration test cuts a real local source, renders through the worker API, validates the output, reopens the database, and completes a trial without publishing. Transcription, selection and editorial verdicts in that test are fixtures; it does not exercise real model inference.

The dashboard trial form/report also passed headless Chrome checks. A real range download from the user's Kick link returned seconds 60–90 as a 30.016-second MP4; evidence is in `output/kick-trial-0.7`. No AI inference or posting was performed in that network smoke check.

Windows workers, real Whisper/Qwen suitability, a 30-clip editorial review and a 24-hour capacity run remain deployment/pilot work. No public posts or clipping.net submissions are part of this release validation.
