from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx

from boti.config import Config, private_write
from boti.db import Store


def setup_logging(root: Path):
    folder = root / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(folder / "boti.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def connect_youtube(
    store: Store, group_id: str, client_file: Path, privacy: str, no_browser=False, channel_id=None
):
    from google_auth_oauthlib.flow import InstalledAppFlow

    account = store.one("SELECT * FROM accounts WHERE group_id=? AND platform='youtube'", (group_id,))
    if not account:
        raise ValueError("Grupi nuk ekziston. Shih ID-të me: boti groups")
    config = json.loads(client_file.read_text(encoding="utf-8-sig"))
    if "installed" not in config:
        raise ValueError("Përdor OAuth Client të tipit Desktop app")
    client = config["installed"]
    if not client.get("project_id"):
        raise ValueError("OAuth client JSON duhet të përmbajë project_id për kuotën e përbashkët")
    pending = store.config.root / "secrets" / f"youtube-pending-{account['id']}.enc"
    secret = store.config.decrypt(pending.read_text()) if pending.exists() else None
    if not secret or secret.get("client_id") != client["client_id"]:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_file),
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
            ],
        )
        credentials = flow.run_local_server(
            port=0, open_browser=not no_browser, access_type="offline", prompt="consent"
        )
        if not credentials.refresh_token:
            raise ValueError("Google nuk ktheu refresh token. Revoko lidhjen e vjetër dhe autorizo përsëri.")
        expiry = credentials.expiry
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        secret = {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "project_id": client["project_id"],
            "refresh_token": credentials.refresh_token,
            "access_token": credentials.token,
            "expires_at": expiry.timestamp() if expiry else 0,
        }
        # Consent is durable even if the owner still needs to create a channel or enable the API.
        private_write(pending, store.config.encrypt(secret))
    with httpx.Client(timeout=30) as http:
        if secret["expires_at"] <= time.time() + 120:
            refreshed = http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": secret["client_id"],
                    "client_secret": secret["client_secret"],
                    "refresh_token": secret["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
            refreshed.raise_for_status()
            value = refreshed.json()
            secret.update(
                access_token=value["access_token"], expires_at=time.time() + value.get("expires_in", 3600)
            )
            private_write(pending, store.config.encrypt(secret))
        response = http.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {secret['access_token']}"},
        )
        response.raise_for_status()
        channels = response.json().get("items", [])
    if not channels:
        raise ValueError(
            "Google u autorizua, por kjo llogari nuk ka kanal YouTube. Krijoje te https://www.youtube.com/create_channel dhe provo përsëri; autorizimi u ruajt i enkriptuar."
        )
    if channel_id:
        channels = [channel for channel in channels if channel["id"] == channel_id]
    if len(channels) != 1:
        choices = "; ".join(f"{item['snippet']['title']} ({item['id']})" for item in channels)
        raise ValueError("Zgjidh kanalin me --channel nga llogaria e autorizuar: " + choices)
    channel = channels[0]
    if store.one(
        "SELECT id FROM accounts WHERE platform='youtube' AND external_id=? AND id!=?",
        (channel["id"], account["id"]),
    ):
        raise ValueError("Ky kanal është lidhur tashmë me një grup tjetër")
    store.execute(
        "UPDATE accounts SET external_id=?,name=?,credentials=?,settings=?,status='connected',error=NULL WHERE id=?",
        (
            channel["id"],
            channel["snippet"]["title"],
            store.config.encrypt(secret),
            json.dumps({"privacy": privacy}),
            account["id"],
        ),
    )
    pending.unlink(missing_ok=True)
    print(f"U lidh YouTube: {channel['snippet']['title']} → {group_id}")


def report(store: Store, since=None):
    since = time.time() - 86400 if since is None else since
    now = time.time()
    tasks = store.all(
        """SELECT kind,count(*) count,avg(finished-started) average_seconds
                         FROM tasks WHERE status='complete' AND finished>=? GROUP BY kind""",
        (since,),
    )
    clips = store.one(
        "SELECT count(*) n FROM clips c JOIN jobs j ON j.id=c.job_id WHERE j.run_mode='production' AND c.status IN ('ready','archived') AND c.updated>=?",
        (since,),
    )["n"]
    hours = max((now - since) / 3600, 0.001)
    reviews = store.one("SELECT count(*) total,coalesce(sum(passed),0) passed FROM reviews")
    pilot_passed = reviews["total"] >= 30 and reviews["passed"] / reviews["total"] >= 0.8
    generation_target_met = hours >= 24 and clips / hours * 24 >= store.settings().target_clips_daily
    return {
        "started": since,
        "ended": now,
        "hours": round(hours, 3),
        "completed_clips": clips,
        "clip_outcomes": store.all(
            "SELECT status,count(*) count FROM clips WHERE updated>=? GROUP BY status", (since,)
        ),
        "automatic_reviews": store.all(
            "SELECT passed,count(*) count FROM quality_reports WHERE created>=? GROUP BY passed", (since,)
        ),
        "observed_clips_per_hour": round(clips / hours, 2),
        "target_daily": store.settings().target_clips_daily,
        "generation_target_met": generation_target_met,
        "pilot_passed": pilot_passed,
        "target_confirmed": generation_target_met and pilot_passed,
        "tasks": tasks,
        "reviews": reviews,
        "failed_tasks": store.one(
            "SELECT count(*) n FROM tasks WHERE status='failed' AND updated>=?", (since,)
        )["n"],
        "publications": store.all(
            "SELECT platform,status,count(*) count FROM publications WHERE updated>=? GROUP BY platform,status",
            (since,),
        ),
        "note": "Kapacitet i matur; nuk garanton cilësinë pa vlerësimin e mostrës. Kuotat e publikimit maten veçmas.",
    }


def main():
    parser = argparse.ArgumentParser(
        prog="boti", description="Main dhe agjentë lokalë për klipe Kick dhe Twitch"
    )
    parser.add_argument("--data", default="data", help="Dosja private e main-it")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Krijo 4 PC dhe 12 grupe")
    init.add_argument(
        "--url", default="http://127.0.0.1:8765", help="Adresa LAN e main-it për skedarët e agjentëve"
    )
    serve = commands.add_parser("serve", help="Nis main-in")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--ssl-cert")
    serve.add_argument("--ssl-key")
    worker = commands.add_parser("worker", help="Nis një agjent")
    worker.add_argument("--config", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="Kontrollo varësitë dhe pajisjet")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument(
        "--probe-ai", action="store_true", help="Provo ngarkimin dhe përgjigjen e modelit lokal"
    )
    commands.add_parser("groups", help="Shfaq ID-të e grupeve")
    youtube = commands.add_parser("connect-youtube", help="Lidh një kanal YouTube me OAuth")
    youtube.add_argument("--group", required=True)
    youtube.add_argument("--client-secrets", type=Path, required=True)
    youtube.add_argument("--privacy", choices=["public", "private", "unlisted"], default="public")
    youtube.add_argument("--no-browser", action="store_true")
    youtube.add_argument("--channel", help="ID-ja e kanalit kur llogaria e autorizuar kthen disa kanale")
    commands.add_parser("report", help="Raporti i 24 orëve të fundit")
    benchmark = commands.add_parser("benchmark", help="Mat kapacitetin gjatë punës reale")
    benchmark.add_argument("--hours", type=float, default=24)
    benchmark.add_argument("--output", type=Path, default=Path("benchmark-report.json"))
    args = parser.parse_args()
    if args.command == "worker":
        from boti.worker import run_worker

        setup_logging(Path("worker-data"))
        run_worker(args.config)
        return
    if args.command == "doctor" and args.config:
        from boti.worker import WorkerConfig, inspect_hardware

        config = WorkerConfig.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
        hardware = inspect_hardware(config, probe_ai=args.probe_ai)
        print(json.dumps(hardware, indent=2, ensure_ascii=False))
        if args.probe_ai and not set(config.capabilities).issubset(hardware["capabilities"]):
            raise SystemExit(1)
        return
    config = Config(args.data)
    store = Store(config)
    if args.command == "init":
        from boti.worker import WorkerConfig

        WorkerConfig(id="check", token="x" * 32, coordinator_url=args.url)
        files = store.seed(args.url)
        print(
            f"U përgatitën 4 PC / 12 grupe / 48 destinacione.\nÇelësi i panelit: {config.root / 'secrets/admin.token'}"
        )
        for path in files:
            print(f"Konfigurimi privat për PC: {path}")
        print("Publikimi mbetet i çaktivizuar deri në lidhjen dhe provën e llogarive.")
    elif args.command == "serve":
        import uvicorn

        from boti.server import create_app

        if bool(args.ssl_cert) != bool(args.ssl_key):
            parser.error("Jep të dyja: --ssl-cert dhe --ssl-key")
        setup_logging(config.root)
        uvicorn.run(
            create_app(config.root, secure_cookies=bool(args.ssl_cert)),
            host=args.host,
            port=args.port,
            workers=1,
            ssl_certfile=args.ssl_cert,
            ssl_keyfile=args.ssl_key,
        )
    elif args.command == "groups":
        store.seed()
        for group in store.all("SELECT * FROM groups ORDER BY id"):
            print(f"{group['id']}  {group['name']}")
    elif args.command == "connect-youtube":
        connect_youtube(store, args.group, args.client_secrets, args.privacy, args.no_browser, args.channel)
    elif args.command == "doctor":
        from boti.media import dependencies

        print(
            json.dumps(
                {
                    "tools": dependencies(),
                    "data": str(config.root),
                    "publishing_enabled": store.settings().publishing_enabled,
                },
                indent=2,
            )
        )
    elif args.command == "report":
        print(json.dumps(report(store), indent=2, ensure_ascii=False))
    elif args.command == "benchmark":
        if not 0 < args.hours <= 168:
            parser.error("--hours duhet të jetë mes 0 dhe 168")
        started = time.time()
        print(f"Po matet kapaciteti për {args.hours} orë. Main-i dhe agjentët duhet të jenë duke punuar.")
        try:
            while time.time() - started < args.hours * 3600:
                time.sleep(min(10, max(0.01, args.hours * 3600 - (time.time() - started))))
        except KeyboardInterrupt:
            pass
        args.output.write_text(
            json.dumps(report(store, started), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Raporti u ruajt: {args.output.resolve()}")
