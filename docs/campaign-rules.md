# Boti 0.5 — Campaign rules drive the edit

One main dispatches; four workers cut and edit. From 0.5 the campaign you select decides **how** every clip is cut, branded and described. Before 0.5 a campaign's rules were a text note read only by the final AI review, and every clip was a fixed 20–60 seconds.

Nothing here contacts clipping.net. You copy the rules from the campaign page into the panel, and you still submit each post URL by hand. Passing Boti's checks does not establish permission or payout eligibility.

## Set up a campaign

1. **Fushatat → + Fushatë.** Paste the campaign's full rules text.
2. **Nxirr rregullat nga teksti.** Boti proposes values it can read from the text: duration range, hashtags, mentions, caption/language requirements, minimum views, pay rate, account limit and platforms. It lists prohibitions and watermark/sound mentions as notes. This is pattern matching, not understanding: compare every field with the campaign page.
3. Fill in what the text cannot give: watermark text, banned words, allowed sources, daily post cap. Save, then reopen the campaign to upload the logo the campaign supplies.
4. Tick the two confirmations when they are true, set the status to `active`, and add a video with this campaign selected.

| Rule | What it controls |
|---|---|
| Minimum / maximum seconds (5–180) | The AI is asked for moments in this range; the main rejects any candidate, repair or output outside it. Analysis windows widen automatically so they always overlap by one full clip. |
| Required language | Passed to Whisper when the streamer profile sets none, and to the AI as a selection requirement. |
| Captions on/off | Burned-in captions are rendered or omitted. |
| Hook title | The clip title is shown in a box for the first 3.5 seconds. |
| Watermark text + position | Burned in for the whole clip, inside the platform-safe area. |
| Logo + position | The uploaded image is re-encoded to PNG on the main, sent to the worker with the segment, and overlaid. A worker that does not receive the exact logo fails the render instead of delivering an unbranded clip. |
| Required hashtags / mentions / text | Always appended to the YouTube description, Instagram caption and TikTok `caption.txt`. The clip's own text is shortened first; required parts are never cut. |
| Banned words | Exact, case-insensitive whole-word match against the transcript, title and caption. Matching moments are dropped before rendering. The AI also receives the list as topics to avoid. |
| Allowed sources | If set, a video is refused unless its channel/uploader, URL or file label contains one of the entries. |
| Max clips per video | Keeps only the highest-scoring moments. |
| Max posts per account per 24 h | YouTube/Instagram uploads for that account wait once the cap is reached. An upload already started is allowed to finish. TikTok is manual, so pace it yourself. |

## Compliance check

Every clip of a campaign job is checked by the main against the job's confirmed rules: duration, banned words, required caption parts, and the overlay evidence reported by the renderer (captions, watermark text, logo hash). The result is shown on the clip card and stored in the quality report.

- A clip that fails is not published, in automatic and manual mode alike.
- Missing or outdated overlays are *fixable*: in automatic mode the clip uses its single correction to render again with the current rules. Wrong duration or a banned word is not fixable and the clip is skipped.
- After you change a campaign and press **Konfirmo rregullat e fundit të fushatës** on the video, automatic jobs recheck their existing clips against the new rules. Manual-mode jobs have no second version: a clip rendered under old overlay rules stays blocked.

The watermark/logo check compares what the worker's renderer reports with the rules. It protects against stale renders and rule changes; it is not image recognition of the final pixels.

## Sources

- **YouTube long-form:** `youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/live/ID` (ended streams). Shorts, playlists and channel pages are refused. H.264/AAC up to 1080p is preferred and merged by FFmpeg. Recent yt-dlp releases need a JavaScript runtime for YouTube; install Deno on the main (`winget install DenoLand.Deno`) if downloads fail with a format or signature error, and keep yt-dlp current with `uv sync --upgrade-package yt-dlp`.
- **Local files:** copy a campaign-supplied video into `data/inbox` on the main and choose it in the panel. The name you give it ("Emri i burimit") keys the streamer profile as `file:<name>`. The file is hard-linked into the job when possible; you may delete it from the inbox once the job is processing.
- **Kick and Twitch VODs:** unchanged.

Only use material the campaign authorises. Set *Burimet e lejuara* so a wrong link cannot be clipped for a campaign by mistake.

## Upgrade from 0.4

Stop main and workers, back up `data`, copy the new code to all five PCs, run `uv sync --locked` on the main and `uv sync --locked --extra worker` on the workers, and start again. There is no database migration: campaigns saved before 0.5 read as 20–60 seconds with captions and no branding, without gaining a new revision. Workers and main must run the same version, because render and analysis tasks now carry the campaign rules.

## Read this before scaling up

clipping.net's clipper terms say low-quality, automated or low-effort posts are removed, each submission must be unique to its account, one clipper account per person, and campaigns may limit accounts per platform (two is a documented example). Enforcement includes forfeited and clawed-back payouts. Boti's default of 12 groups and a 1,000-clips-a-day target is machine capacity, not something those terms allow you to post. Set **Kufiri i llogarive / platformë** and the daily post cap from the real campaign page, review what the PCs produce, and ask the campaign or platform whether your level of automation is accepted before ticking the automation confirmation.

## Not verified

Implemented and tested with real FFmpeg and simulated AI/platform responses (see `validation.md`). Not exercised: a live YouTube download, Windows workers, Whisper/Qwen on real recordings, real posts, or how a real campaign reviewer judges the output.
