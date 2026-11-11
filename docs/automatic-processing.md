# Boti 0.4 — Automatic processing

The main owns ingestion, durable state, version identity and publication gates. Workers perform one heavy task at a time. `quality` performs fresh Whisper transcription first, releases that model, then queries Ollama. `repair` retranscribes the original prepared segment when captions need correction. No paid AI endpoint is used.

## Start and upgrade

1. Stop main/workers, preserve the entire `data` directory (including its encryption keys), copy the new source bundle and run `uv sync` on main / `uv sync --extra worker` on workers. Start the main again. SQLite migration is transactional and idempotent; job primary keys and all dependent links remain unchanged. Existing jobs retain manual mode and existing publisher settings are preserved.
2. Use the Windows setup/Task Scheduler scripts from README. Old worker configurations with `analyze` gain `quality` when Ollama, Whisper and FFmpeg are present; `transcribe` gains `repair`. New configurations advertise all five roles. Run `boti doctor --config <worker.json> --probe-ai` on each PC.
3. Configure the real campaign rules, allowed groups/platforms and automation confirmation. Connect accounts and enable the existing publishing switch when ready. Missing connections do not lose completed clips.
4. Select a campaign, choose Automatic and paste a completed Kick/Twitch VOD link. YouTube/Instagram outboxes wait for permissions, quota and current QA. TikTok packages and clipping.net URL submissions keep their manual completion steps.

Local inference was not installed on the development Mac. Do not treat a successful code test as a passed real-world editorial pilot. Start with one Windows worker; inspect 30 real outputs and run the existing 24-hour benchmark before expanding to all four workers.

## API

`POST /api/jobs`:

```json
{
  "url": "https://kick.com/deenthegreat/videos/01a1b7a3-f0e8-7254-aef0-2384158ccac2",
  "provider": "kick",
  "review_mode": "automatic",
  "group_ids": ["pc-1-g1"],
  "campaign_id": null
}
```

`provider` is optional and inferred; a mismatch is rejected. `review_mode` defaults to `automatic` for new API submissions. Reimporting an old VOD returns its existing job without changing mode, campaign or groups. The internal Store default stays manual for backward compatibility. URLs must use exact allowed HTTPS hosts and VOD paths. Live channel pages, credentials, explicit ports and foreign hosts are rejected.

`GET /api/clips` includes `current_version`, `quality_reason`, `versions` and `quality_reports`. A report records version, asset ID, SHA256, metadata hash, campaign rules revision, technical evidence and editorial verdict. Human `reviews` remain a separate table. `/api/snapshot` exposes accepted/skipped/technical-failed job counts and per-stage queue metrics. `boti report` includes clip outcomes and machine review totals separately from the human pilot sample.

Worker protocol is unchanged except for two new task kinds, `quality` and `repair`, and a five-capability heartbeat. All results still require the current unexpired lease. A repair response may only update the source-derived times/segments; the server retains title, destination and other metadata. Output upload still performs main-side full decode and format verification.

## Durable transitions

```text
preparing → rendering → checking → approved → ready → platform outboxes
                           │
                           └─ fail v1 → correcting → preparing v2 → rendering → checking
                                                                               │
                                                                               └─ fail → skipped
```

Quality tasks identify the exact clip version/rules revision/metadata hash. Corrections create version 2 only; duplicate coordinator observations and stale worker results cannot create version 3. Technical retries use the existing three-attempt lease queue and never consume the editorial correction allowance. Exhausted render/review/repair failures mark only that clip `technical_failed`. Transcription/analysis failures still fail the source job because candidate planning cannot safely finish without them.

Prepared render/review/correction work shares the existing global bound. Inputs survive until correction and export are durable. A complete download has an atomic local checkpoint so a preparation restart can proceed without another provider call. Partial downloads retain yt-dlp continuation state and bounded request/fragment retries. Disk pressure pauses preparation and ingestion.

## Quality and publication rules

- Technical: 20–60 second target, full decoding, dimensions/codecs, frame rate/pixel format, audio format, measured loudness (within 2 LU of −16, true peak ≤ −0.5 dB), caption timing bounds, fresh-ASR lexical/timing agreement. Truly silent visual moments can qualify. ASR agreement currently requires ratio ≥0.65 and median matched-word offset ≤0.65 seconds; these conservative heuristics require real pilot tuning across languages/background noise.
- Editorial: Qwen receives source transcript, fresh rendered-audio transcript, six chronological portrait frames of the final output, technical findings and campaign editorial text. Passing requires the model's approval and configured score (initially 75/100), plus all deterministic checks. Sparse frames and local models can miss errors; neither is a guarantee of quality, virality or payout eligibility.
- Corrections: retranscribe the original audio for captions, align to existing source sentence/word boundaries (bounded shifts), or use full-frame composition. Every corrected output uses conservative composition and is reevaluated. If a missing payoff cannot be recovered from those source boundaries, the second review rejects it. No generated dialogue, synthetic gameplay or fabricated context is inserted.
- Publishing checks current artifact bytes/metadata/version, current rules, active campaign, eligibility, confirmation flags and human veto. A positive human review does not bypass failed machine QA for an automatic job. Completed outputs alone never establish platform permission or campaign payout eligibility.
- Rule changes immediately block the old approval. The existing “confirm latest campaign rules” action updates the job snapshot and schedules automatic rechecking of its existing artifact. Negative manual reviews survive this action. If an outbox already references that artifact, failure of a later rule recheck skips/holds it rather than changing media underneath a potentially live upload session. Existing upload IDs/state are preserved for reconciliation.
- TikTok export downloads are gated by current eligibility too. YouTube/Instagram retain resumable upload/recovery and quota logic. No new TikTok posting or clipping.net private-panel automation is introduced.

Historical metadata/reports remain after media retention. Superseded rejected render files are removed after output retention only once the job is terminal; current assets with unresolved publications remain protected by the existing retention rules.

## Verified boundaries

See [validation.md](validation.md) for test evidence and real Kick sample results. The 30-clip human pilot is an acceptance/measurement exercise, not an approval requirement on each production clip. Windows hardware inference, real account permissions and sustained capacity must still be measured on the user's machines. Payments and 1,000+ accepted clips/day are not guaranteed.

Sources for integrations: [official Kick extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/kick.py), [clipping.net terms](https://clipping.net/policies/terms-of-service), [TikTok developer guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines).
