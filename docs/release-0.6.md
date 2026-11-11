# Boti 0.6 — Stability before the pilot

This release fixes two reproduced defects: substring source authorization and partial-byte file identities. It does not add paid AI services, automatic clipping.net access or a claim of production throughput.

## Source restrictions

- A plain name matches a complete name/handle, ignoring case and an optional leading `@`. Local labels are operator-provided, not independently verified ownership.
- Prefer `https://kick.com/creator`, `https://twitch.tv/creator`, `https://youtube.com/@handle` or a YouTube `/channel/ID` URL when platform scoping matters. A Kick channel URL never grants a similarly named Twitch channel. YouTube channel IDs and video IDs retain case sensitivity.
- An exact completed-video URL grants only that video; adding a time query does not change its identity. URL credentials, lookalike hosts and substring prefixes/suffixes do not confer a match.
- Twitch/YouTube channel URLs can be matched from extractor-supplied uploader/channel URLs. New downloads retain these identities. Older jobs can use retained channel IDs where sufficient; incomplete old metadata may require review instead of automatic acceptance.
- Restart checkpoints and publication gates recheck restrictions. This avoids grandfathering a source incorrectly accepted by 0.5.

## File identity and preservation

New local imports use `sha256:<full digest>` over every byte, in 1 MB chunks. File statistics are compared before/after the read to reject observed mutation. The full identity is verified on the main's private copy before transcription. Mutating an inbox file after enqueueing fails that job with an explicit message; submit the new file as a new job. Editing the inbox after preparation cannot alter the private original.

A full read takes time proportional to file size. The main now reserves space and copies the input, rather than hard-linking the mutable inbox file. Keep the usual free-space margin plus the original-copy requirements.

On reimport, a matching 0.5 partial fingerprint only locates a possible old job. The old job's **retained original** is fully hashed before upgrading its identity. Primary job IDs, campaign bindings, tasks, clips and publication histories remain unchanged. A different full hash creates a distinct job; a matching hash reuses the old job, still respecting its existing campaign assignment. If the retained original is gone, an ambiguous matching old fingerprint blocks the reimport with an explanation. Restore that original through the normal backup process; never substitute a different video to bypass the check. Historical 0.5 originals that were already changed through their old hard links cannot be forensically reconstructed by this migration.

## Runtime and pilot

`GET /api/snapshot` now returns `version` and `readiness` (`workers_ready`, `missing_capabilities`). Readiness aggregates capabilities from unpaused agents seen within 30 seconds. It does not enable publishing, verify accounts or certify a model's output. The panel shows these facts directly.

Upgrade main and workers from the same 0.6 bundle, keeping the existing data/secrets and backing up SQLite while the coordinator is stopped. Start the main, verify **Boti 0.6.0** in the dashboard, then connect one Windows worker with Whisper/Ollama/FFmpeg. Confirm the campaign's actual rules and allowed accounts, inspect 30 real clips and run the 24-hour benchmark before scaling. The code and local technical tests are complete; that physical deployment/model pilot is not performed by this release.
