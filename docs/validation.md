# Verification and remaining setup

## Version 0.7: bounded trials (10 November 2026)

156 automated tests passed locally with full FFmpeg. The 18 trial cases cover old-schema migration and identity separation, bounds, missing workers, no queue starvation, range requests/checkpoint reuse, rejecting incorrect ranges, low disk, candidate caps, source retention, report access and publication isolation even with publishing enabled. A real local-source integration cuts the interval, uses the worker API to render, uploads/validates the actual output, reopens SQLite and completes the trial without an outbox. AI transcript/candidate/verdict responses are fixtures.

Headless Chrome passed the trial form/report checks alongside existing desktop/mobile checks, with no JavaScript errors. The browser submits a fixture job, verifies the selected start/duration/limit, checks original timestamps and the same-origin video URL, and escapes untrusted titles; it does not create live jobs.

The actual 0.7 range-download path was also exercised against the user's Kick VOD: source seconds 60–90 yielded a 30.016-second, 16,659,799-byte MP4. `output/kick-trial-0.7/sample.mp4` and `report.json` retain the evidence. This was a real network range download, not model selection, editorial approval or a post.

The trial feature does not establish real editorial quality or Windows/GPU capacity. Real Whisper/Qwen inference and the 30-clip/24-hour pilot remain outstanding.

## Version 0.6: stability (10 November 2026)

138 automated tests passed locally on macOS/Python 3.12 with an isolated full FFmpeg. The 29 new cases in `tests/test_stability.py` cover exact/platform-scoped source matching, VOD-vs-channel restrictions, same-size file collisions in both the old tail and middle blind spots, filename-independent SHA256, verified legacy-job identity upgrades, missing-original ambiguity, independent retained copies, queued-file mutation, checkpoint/publication source rechecks and live-worker readiness. Existing real FFmpeg and simulated publication tests remain in the suite.

The dashboard exposes the active server version and missing live-worker capabilities. This status is based on authenticated agent heartbeats, not a new real-model inference benchmark. Models, Windows deployment, 30 real editorial reviews and the 24-hour trial remain outstanding. No public posts were made.

## Version 0.5: campaign rules (10 November 2026)

109 automated tests passed on Linux with Python 3.12 and a full FFmpeg (libass); `ruff check` and `ruff format --check` are clean. 21 of them are new in `tests/test_rules.py`.

Checked with real FFmpeg, from a file in `data/inbox` through the worker HTTP API to the exported TikTok package: an 8–15 second campaign range reaches the analysis prompt and is enforced by the main against a worker's out-of-range candidate; the render carries the uploaded logo (verified by reading pixels of an output frame), the watermark and hook lines in the subtitle script, and reports them as overlay evidence; the final caption carries the required mention, disclosure and hashtags; the main's compliance check passes and publishing is allowed.

Checked with fixtures: rules-text extraction, rule normalisation and bounds, exact banned-word filtering and max-clips at planning, a stale watermark triggering exactly one re-render while a banned word skips the clip, the per-account daily cap, logo upload re-encoding and revision handling, pre-0.5 campaign documents reading as defaults without a new revision, YouTube link acceptance/refusal and inbox path safety. A headless Chrome run exercised the campaign form (extract, save, logo upload) and an inbox job with no JavaScript errors.

Not exercised for 0.5: a live YouTube download (network-restricted build machine; the adapter and format selection are tested only against a simulated yt-dlp), any Windows worker, Whisper/Qwen on real recordings, real uploads, and whether a real campaign accepts the result. The rules-text extractor is heuristic and was tested on a handful of English phrasings.


Version 0.4 was checked locally on macOS with Python 3.12: 88 automated tests passed. The target deployment is five Windows computers on a trusted LAN.

Campaign checks cover authenticated local records, rate units and account bounds, immutable job assignment, persistence after reopening SQLite, rule revisions and renewed clip review, and idempotent manual submission history. No clipping.net connection, submission or payment is performed by these checks. Kick ingestion and automatic rendered-output review are implemented; the timeline editor and earnings ledger remain roadmap work.

Verified:

- Migration from the 0.3 schema preserves job/task foreign keys and manual review mode. Provider/video identities are unique; repeated import and complete-download restart reuse one source.
- Automatic direct acceptance, one successful repair, second rejection -> skipped, and separate exhausted infrastructure retries -> technical_failed. Neither outcome fails the entire VOD. Reopening SQLite and repeating coordinator ticks do not duplicate repairs/outboxes.
- Post-render quality lease fencing, backpressure including waiting reviews, automatic publication without human review, exact metadata/SHA/rules binding, same-size file changes, and manual veto retained across rule revisions.
- Integration runs start with a Kick/Twitch link through simulated yt-dlp and use real FFmpeg from source through output review and export. Model responses and fresh ASR are fixtures; portrait storyboards, decoding, loudness and subtitle rendering run for real.

- Durable task claims under concurrent workers, one active task per worker, expiring leases, stale-result fencing, restart recovery, pause and disk backpressure.
- Global active-VOD, analysis-proxy and prepared-render bounds, including restart and multiple jobs; aging tasks ahead of fresh renders; measured per-stage durations and live capability counts.
- Admin/worker separation, signed browser sessions, CSRF, Kick/Twitch URL restrictions and camera bounds.
- Real FFmpeg ingestion, chunk audio, proxy generation, scene/audio cues, visual storyboards, accurate source cuts, caption rendering, both fit and camera/game layouts, coordinator decoding/format checks, TikTok export and retention.
- Sentence/pause cut alignment, word-boundary protection, verifying overlaps before final deduplication, preserved verification reasons, and passing source language/vocabulary to Whisper (mock model).
- Caption phrase length, pause/punctuation splitting, two-line wrapping, real-word highlighting, plain-text fallback and ASS-injection protection. Both integration layouts render the new timed captions with real libass.
- Two-pass audio measurement/normalization: the synthetic audio in both real video outputs was measured within 1 LU of the −16 LUFS target. Non-finite silence measurements fall back without invalid filter arguments. This is not an acoustic-quality assessment of real streams.
- Failed clip reviews hold YouTube/Instagram publications while preserving upload state; accepting again resumes the queue. The redundant prepared segment is removed only after the finished output and export are durable.
- YouTube resume after a lost completion response, shared project quota, upload processing/rejection, ambiguous-session handling and Instagram recovery after a lost publish response. Platform traffic is simulated with HTTP transports.
- Structured local-AI requests and output validation. Test candidates/approval responses are simulated; Whisper output is supplied as a fixture in the integration test.
- Headless Chrome at desktop and mobile widths: login, worker cards, stage metrics, new settings, navigation, account dialog, review notes/hold/release with fixture responses, no JavaScript errors or horizontal overflow. Browser review fixtures do not write into the live main database.
- Wheel packaging includes the dashboard assets. The Windows source ZIP excludes credentials, models and runtime data.

The host FFmpeg build lacks libass. The real video tests were therefore run with an isolated imageio-ffmpeg full binary on PATH, without replacing the system FFmpeg. To reproduce on this host:

```sh
uv run --with imageio-ffmpeg python -c 'import imageio_ffmpeg,os,pathlib,subprocess,sys,tempfile; d=tempfile.TemporaryDirectory(); pathlib.Path(d.name,"ffmpeg").symlink_to(imageio_ffmpeg.get_ffmpeg_exe()); env=dict(os.environ,PATH=d.name+os.pathsep+os.environ["PATH"]); result=subprocess.run([sys.executable,"-m","pytest","-q","--tb=short"],env=env); sys.exit(result.returncode)'
```

With a full FFmpeg already on PATH, use `uv run pytest -q` directly. Browser verification: start the main server, then `uv run --with playwright scripts/smoke_ui.py` with Chrome installed.

Not validated by these checks:

- Windows Task Scheduler/ACL execution, CUDA/NVENC on the user's PCs, or the real five-computer network. Windows/Linux CI configuration is supplied; it was not run remotely during implementation.
- Whisper medium and Qwen inference on the user's actual recordings, the editorial quality of 30 clips, or sustained 24-hour capacity. Models must be installed and the local probe/pilot performed on those PCs.
- Actual Google/Meta OAuth permissions, app review, account quotas or public posts. Accounts must be connected and tested by their owner. Publishing is disabled in the initial configuration.

The capacity report confirms the target only when the measured daily rate meets the configured goal over at least 24 hours and the pilot has at least 30 distinct reviewed clips with an 80% pass rate. It does not create clips merely to meet that count.

## Real Kick connection: 9 November 2026

The user-supplied completed VOD `https://kick.com/deenthegreat/videos/01a1b7a3-f0e8-7254-aef0-2384158ccac2` was probed with the installed official yt-dlp KickVOD extractor. It returned “WE IN THE CITY 🗽”, five formats and 150,013 seconds. Its HLS media playlist had an end marker and a summed duration of 150,013.484 seconds. A 24-second 720p segment was actually downloaded.

That sample was rendered with the conservative full-frame layout to 1080×1920 H.264/AAC and fully decoded. The measured output audio was −15.09 LUFS (target −16). Evidence is in `output/kick-pilot/probe.json` and `render-report.json`; `sample.mp4` and `render-check.mp4` are local technical artifacts excluded from the Windows bundle. No subtitle text was invented for this real sample.

This is a real connection/download/render smoke test, **not** a complete VOD run or approved campaign clip. This Mac has neither faster-whisper installed nor a running Ollama model. Consequently the real 30-clip editorial pilot, model/hardware suitability and 24-hour throughput trial remain unperformed. No platform posts or clipping.net submissions were made.
