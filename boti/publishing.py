from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from boti.db import Store, uid
from boti.pipeline import caption
from boti.rules import clip_rules, final_caption, final_hashtags


class Deferred(Exception):
    def __init__(self, message, seconds=60):
        super().__init__(message)
        self.seconds = seconds


class Reconnect(Exception):
    pass


class Ambiguous(Exception):
    pass


def check(response: httpx.Response):
    if response.is_success or response.status_code == 308:
        return response
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {"message": str(error)}
    message = str(error.get("message", f"Platforma ktheu HTTP {response.status_code}"))[:400]
    reason = str(error)
    meta_limited = error.get("code") in {4, 17, 32, 613, 80002} or (
        error.get("code") == 9 and error.get("error_subcode") == 2207042
    )
    if (
        response.status_code == 429
        or meta_limited
        or any(v in reason for v in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"))
    ):
        wait = response.headers.get("Retry-After", "3600")
        raise Deferred(message, min(86400, max(30, int(wait))) if wait.isdigit() else 3600)
    if response.status_code == 401 or error.get("code") == 190:
        raise Reconnect("Lidhja skadoi; rilidhe llogarinë")
    if response.status_code >= 500:
        raise Deferred(message, 120)
    if error.get("is_transient") is True:
        raise Deferred(message, 120)
    raise ValueError(message)


def quota_window(now: float):
    zone = ZoneInfo("America/Los_Angeles")
    current = datetime.fromtimestamp(now, zone)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    reset = (start + timedelta(days=1)).timestamp()
    return start.timestamp(), reset


def youtube_session_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "youtube.googleapis.com"}:
        raise ValueError("YouTube returned an unexpected upload host")
    return url


class Publisher:
    def __init__(self, store: Store, client: httpx.Client | None = None):
        self.store = store
        self.client = client or httpx.Client(timeout=httpx.Timeout(180, connect=20), follow_redirects=False)

    def save_state(self, publication_id: str, state: dict):
        self.store.execute(
            "UPDATE publications SET state=?,updated=? WHERE id=?",
            (self.store.config.encrypt(state), time.time(), publication_id),
        )

    def state(self, publication: dict):
        value = publication["state"]
        return {} if value == "{}" else self.store.config.decrypt(value)

    def reserve_youtube(self, project_id: str, publication_id: str):
        now = time.time()
        start, reset = quota_window(now)
        with self.store.connect(immediate=True) as db:
            count = db.execute(
                "SELECT count(*) FROM quota_events WHERE project_id=? AND created>=?", (project_id, start)
            ).fetchone()[0]
            if count >= self.store.settings().youtube_daily_uploads:
                raise Deferred("Kuota ditore e projektit YouTube është mbushur", reset - now + 5)
            db.execute("INSERT INTO quota_events VALUES (?,?,?,?)", (uid(), project_id, publication_id, now))

    def access_token(self, account: dict, credentials: dict) -> str:
        if credentials.get("expires_at", 0) > time.time() + 120 and credentials.get("access_token"):
            return credentials["access_token"]
        response = self.client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "refresh_token": credentials["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if response.status_code == 400:
            raise Reconnect("Autorizimi i YouTube u revokua; lidhe përsëri kanalin")
        check(response)
        value = response.json()
        credentials.update(
            access_token=value["access_token"], expires_at=time.time() + value.get("expires_in", 3600)
        )
        self.store.execute(
            "UPDATE accounts SET credentials=? WHERE id=?",
            (self.store.config.encrypt(credentials), account["id"]),
        )
        return credentials["access_token"]

    def tick(self):
        settings = self.store.settings()
        if settings.paused or not settings.publishing_enabled:
            return
        publication = self.store.one(
            """SELECT p.* FROM publications p
            JOIN accounts a ON a.id=p.account_id JOIN groups g ON g.id=a.group_id
            LEFT JOIN reviews r ON r.clip_id=p.clip_id
            WHERE p.status IN ('queued','processing') AND p.not_before<=? AND a.status='connected'
            AND g.paused=0 AND coalesce(r.passed,1)=1
            ORDER BY p.updated,p.created LIMIT 1""",
            (time.time(),),
        )
        if not publication:
            return
        account = self.store.one("SELECT * FROM accounts WHERE id=?", (publication["account_id"],))
        clip = self.store.one("SELECT * FROM clips WHERE id=?", (publication["clip_id"],))
        from boti.campaigns import campaign_for_job, publish_allowed

        campaign = campaign_for_job(self.store, clip["job_id"])
        if not publish_allowed(self.store, clip) or (
            campaign and publication["platform"] not in json.loads(campaign["current_rules"])["platforms"]
        ):
            self.defer(
                publication["id"], "Fushata pret konfirmimin e rregullave, automatizimit ose klipit", 60
            )
            return
        asset = self.store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],))
        state = self.state(publication)
        preferences = json.loads(account["settings"])
        if not state and preferences.get("provider") == "upload_post":
            recent = self.store.one(
                """SELECT count(*) n,max(updated) last FROM publications
                WHERE account_id=? AND id!=? AND updated>? AND
                (status IN ('published','needs_reconciliation') OR (status IN ('processing','queued') AND state!='{}'))""",
                (account["id"], publication["id"], time.time() - 86400),
            )
            if recent["n"] >= preferences.get("daily_limit", 3) or (
                recent["last"] and time.time() - recent["last"] < preferences.get("interval_minutes", 60) * 60
            ):
                self.defer(publication["id"], "Postimi pret kufirin ditor ose intervalin e llogarisë", 300)
                return
        rules = clip_rules(campaign["rules_snapshot"]) if campaign else None
        if rules and rules.max_posts_per_account_daily and not state:
            # Campaign pacing per account. An upload already started is always allowed to finish.
            recent = self.store.one(
                """SELECT count(*) n FROM publications WHERE account_id=? AND id!=? AND updated>?
                AND (status IN ('published','needs_reconciliation') OR (status='processing' AND state!='{}'))""",
                (account["id"], publication["id"], time.time() - 86400),
            )["n"]
            if recent >= rules.max_posts_per_account_daily:
                self.defer(publication["id"], "Kufiri ditor i postimeve për këtë llogari u arrit", 1800)
                return
        from boti.archive import request_restore

        if not asset or not request_restore(self.store, asset):
            self.defer(publication["id"], "Videoja pret rikthimin nga PC-ja që e editoi", 30)
            return
        self.store.execute(
            "UPDATE publications SET status='processing',updated=? WHERE id=?",
            (time.time(), publication["id"]),
        )
        try:
            credentials = self.store.config.decrypt(account["credentials"])
            path = self.store.config.path(asset["path"])
            metadata = json.loads(clip["metadata"])
            if rules:
                # Required campaign tags, mentions and disclosure are part of every post text.
                metadata["post_text"] = final_caption(metadata, rules, publication["platform"])
                metadata["hashtags"] = final_hashtags(metadata, rules)
            if credentials.get("provider") == "upload_post":
                from boti.upload_post import publish

                metadata["post_text"] = final_caption(metadata, rules, publication["platform"])
                result = publish(self, publication, account, credentials, path, metadata, state)
            elif publication["platform"] == "youtube":
                result = self.youtube(publication, account, credentials, path, metadata, state)
            elif publication["platform"] == "instagram":
                result = self.instagram(publication, account, credentials, path, metadata, state)
            else:
                raise Reconnect("Lidhe platformën me Upload-Post për publikim automatik")
            if result:
                self.store.execute(
                    """UPDATE publications SET status='published',external_id=?,url=?,error=NULL,
                    updated=? WHERE id=?""",
                    (result.get("id"), result.get("url"), time.time(), publication["id"]),
                )
                self.store.event(f"U publikua në {publication['platform']}", job_id=clip["job_id"])
            else:
                self.store.execute(
                    "UPDATE publications SET not_before=?,updated=? WHERE id=?",
                    (time.time() + 10, time.time(), publication["id"]),
                )
        except Deferred as exc:
            self.defer(publication["id"], str(exc), exc.seconds)
        except Reconnect as exc:
            self.store.execute(
                "UPDATE accounts SET status='needs_attention',error=? WHERE id=?", (str(exc), account["id"])
            )
            self.defer(publication["id"], str(exc), 300)
        except Ambiguous as exc:
            self.store.execute(
                "UPDATE publications SET status='needs_reconciliation',error=?,updated=? WHERE id=?",
                (str(exc), time.time(), publication["id"]),
            )
        except httpx.TransportError:
            self.defer(publication["id"], "Lidhja u ndërpre; gjendja kontrollohet para vazhdimit", 60)
        except Exception as exc:
            self.store.execute(
                "UPDATE publications SET status='failed',error=?,updated=? WHERE id=?",
                (str(exc)[:1000], time.time(), publication["id"]),
            )

    def defer(self, publication_id, message, seconds):
        self.store.execute(
            "UPDATE publications SET status='queued',error=?,not_before=?,updated=? WHERE id=?",
            (message, time.time() + seconds, time.time(), publication_id),
        )

    def check_quality(self, publication):
        from boti.campaigns import publish_allowed

        clip = self.store.one("SELECT * FROM clips WHERE id=?", (publication["clip_id"],))
        if not publish_allowed(self.store, clip):
            raise Deferred("Fushata pret kontroll", 60)
        review = self.store.one("SELECT passed FROM reviews WHERE clip_id=?", (publication["clip_id"],))
        if review and not review["passed"]:
            raise Deferred("Klipi kërkon përmirësim; publikimi pret vlerësimin 'Në rregull'", 60)

    def youtube(self, publication, account, credentials, path, metadata, state):
        self.check_quality(publication)
        token = self.access_token(account, credentials)
        headers = {"Authorization": f"Bearer {token}"}
        size = path.stat().st_size
        if state.get("video_id"):
            return self.youtube_result(state["video_id"], headers)
        if not state.get("session_url"):
            if publication["attempts"] >= 3:
                raise ValueError("Ngarkimi nuk u nis pas tri provave. Kontrollo lidhjen para riprovimit.")
            self.reserve_youtube(credentials["project_id"], publication["id"])
            self.store.execute("UPDATE publications SET attempts=attempts+1 WHERE id=?", (publication["id"],))
            self.save_state(publication["id"], {"phase": "initiating"})
            response = check(
                self.client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers={
                        **headers,
                        "X-Upload-Content-Length": str(size),
                        "X-Upload-Content-Type": "video/mp4",
                    },
                    json={
                        "snippet": {
                            "title": metadata["title"][:100],
                            "description": metadata.get("post_text") or caption(metadata),
                            "categoryId": "20" if metadata.get("content_type") == "gaming" else "22",
                            "tags": metadata.get("hashtags", []),
                        },
                        "status": {
                            "privacyStatus": json.loads(account["settings"]).get("privacy", "public"),
                            "selfDeclaredMadeForKids": False,
                        },
                    },
                )
            )
            state = {
                "session_url": youtube_session_url(response.headers["Location"]),
                "phase": "upload",
                "bytes_started": False,
            }
            self.save_state(publication["id"], state)
        session = youtube_session_url(state["session_url"])
        # This status request also recovers a lost final PUT response after a restart.
        response = self.client.put(
            session,
            headers={**headers, "Content-Length": "0", "Content-Range": f"bytes */{size}"},
            content=b"",
        )
        if response.status_code in (200, 201):
            video_id = response.json()["id"]
            state["video_id"] = video_id
            self.save_state(publication["id"], state)
            return self.youtube_result(video_id, headers)
        if response.status_code in (404, 410):
            if state.get("bytes_started"):
                raise Ambiguous(
                    "Sesioni YouTube skadoi pas nisjes së transferimit. Verifiko kanalin para riprovimit."
                )
            self.save_state(publication["id"], {})
            raise Deferred("Sesioni i pambushur skadoi; do të hapet një tjetër", 60)
        check(response)
        if response.status_code != 308:
            raise ValueError("Unexpected YouTube upload status")
        match = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
        offset = int(match.group(1)) + 1 if match else 0
        if offset >= size:
            raise Deferred("YouTube po përfundon përpunimin", 15)
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(8 * 1024 * 1024)
        self.check_quality(publication)
        state["bytes_started"] = True
        self.save_state(publication["id"], state)
        response = self.client.put(
            session,
            headers={
                **headers,
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}",
            },
            content=chunk,
        )
        check(response)
        if response.status_code in (200, 201):
            video_id = response.json()["id"]
            state["video_id"] = video_id
            self.save_state(publication["id"], state)
            return self.youtube_result(video_id, headers)
        return None

    def youtube_result(self, video_id, headers):
        response = check(
            self.client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                headers=headers,
                params={"part": "status,processingDetails", "id": video_id},
            )
        )
        items = response.json().get("items", [])
        if not items:
            raise Deferred("YouTube ende nuk e ktheu gjendjen e videos së ngarkuar", 60)
        status = items[0].get("status", {})
        processing = items[0].get("processingDetails", {}).get("processingStatus")
        if status.get("uploadStatus") in {"failed", "rejected", "deleted"} or processing in {
            "failed",
            "terminated",
        }:
            reason = status.get("rejectionReason") or status.get("failureReason") or processing
            raise ValueError(f"YouTube nuk e pranoi videon pas ngarkimit: {reason}")
        if status.get("uploadStatus") == "processed" or processing == "succeeded":
            return {"id": video_id, "url": f"https://www.youtube.com/shorts/{video_id}"}
        raise Deferred("YouTube po përpunon videon e ngarkuar", 30)

    def instagram(self, publication, account, credentials, path, metadata, state):
        self.check_quality(publication)
        # Facebook Login for Business permits local binary uploads; no public media server is needed.
        version = self.store.settings().instagram_api_version
        base = f"https://graph.facebook.com/{version}"
        ig_id = account["external_id"]
        if not re.fullmatch(r"\d+", ig_id or ""):
            raise Reconnect("Mungon identifikuesi numerik i llogarisë profesionale Instagram")
        token = credentials["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        if state.get("media_id"):
            return self.instagram_result(base, state["media_id"], headers)
        if not state.get("container_id"):
            if publication["attempts"] >= 3:
                raise ValueError(
                    "Instagram nuk e përpunoi videon pas tri provave. Kontrollo llogarinë dhe videon."
                )
            response = check(
                self.client.get(
                    f"{base}/{ig_id}/content_publishing_limit",
                    headers=headers,
                    params={"fields": "quota_usage,config"},
                )
            )
            data = response.json().get("data", [])
            total = data[0].get("config", {}).get("quota_total") if data else None
            if not isinstance(total, int) or total <= 0:
                raise ValueError("Meta nuk ktheu kuotën e publikimit. Kontrollo lejet e llogarisë.")
            if data[0].get("quota_usage", 0) >= total:
                raise Deferred("Kuota e Instagram është mbushur", 3600)
            self.store.execute("UPDATE publications SET attempts=attempts+1 WHERE id=?", (publication["id"],))
            response = check(
                self.client.post(
                    f"{base}/{ig_id}/media",
                    headers=headers,
                    data={
                        "media_type": "REELS",
                        "upload_type": "resumable",
                        "caption": metadata.get("post_text") or caption(metadata),
                        "share_to_feed": "true",
                    },
                )
            )
            container = response.json()
            upload_uri = container.get("uri")
            if not upload_uri:
                raise ValueError(
                    "Meta nuk ktheu URI për ngarkim lokal. Kontrollo Facebook Login for Business."
                )
            parsed = urlparse(upload_uri)
            if parsed.scheme != "https" or parsed.hostname != "rupload.facebook.com":
                raise ValueError("Meta returned an unexpected upload host")
            state = {
                "container_id": container["id"],
                "upload_uri": upload_uri,
                "phase": "upload_pending",
                "created": time.time(),
            }
            self.save_state(publication["id"], state)
        container_id = state["container_id"]
        if state["phase"] == "upload_pending":
            state["phase"] = "upload_uncertain"
            self.save_state(publication["id"], state)
            with path.open("rb") as handle:
                response = check(
                    self.client.post(
                        state["upload_uri"],
                        headers={
                            "Authorization": f"OAuth {token}",
                            "offset": "0",
                            "file_size": str(path.stat().st_size),
                            "Content-Length": str(path.stat().st_size),
                            "Content-Type": "application/octet-stream",
                        },
                        content=handle,
                    )
                )
            if response.json().get("success") is not True:
                raise ValueError("Meta nuk konfirmoi transferimin e videos")
            state["phase"] = "processing"
            self.save_state(publication["id"], state)
            raise Deferred("Instagram po përpunon videon", 60)
        response = check(
            self.client.get(
                f"{base}/{container_id}", headers=headers, params={"fields": "status_code,status"}
            )
        )
        code = response.json().get("status_code")
        if code == "PUBLISHED":
            # A lost publish response must never create a second container/post.
            return {"id": state.get("media_id", container_id), "url": None}
        if code in ("ERROR", "EXPIRED"):
            if state.get("publish_started"):
                raise Ambiguous("Kontrollo publikimin Instagram para krijimit të një kontejneri tjetër")
            self.save_state(publication["id"], {})
            raise Deferred(f"Instagram {code}; ngarkimi i papublikuar do të riprovohet", 300)
        if code != "FINISHED":
            if state["phase"] == "upload_uncertain" and time.time() - state["created"] > 300:
                # No publish call was made. An incomplete transfer can safely start with a new container.
                self.save_state(publication["id"], {})
            raise Deferred("Instagram po përpunon videon", 60)
        if state.get("publish_started"):
            raise Ambiguous("Përgjigjja e publikimit Instagram humbi; verifiko llogarinë para riprovimit")
        self.check_quality(publication)
        state["publish_started"] = True
        self.save_state(publication["id"], state)
        response = self.client.post(
            f"{base}/{ig_id}/media_publish", headers=headers, data={"creation_id": container_id}
        )
        try:
            check(response)
        except Deferred:
            if response.status_code in {400, 429}:
                state["publish_started"] = False
                self.save_state(publication["id"], state)
            raise
        state["media_id"] = response.json()["id"]
        self.save_state(publication["id"], state)
        return self.instagram_result(base, state["media_id"], headers)

    def instagram_result(self, base, media_id, headers):
        response = check(
            self.client.get(f"{base}/{media_id}", headers=headers, params={"fields": "permalink"})
        )
        return {"id": media_id, "url": response.json().get("permalink")}
