"""Optional browser check: uv run --with playwright scripts/smoke_ui.py --data data."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/screenshots"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.locator("#token").fill((args.data / "secrets/admin.token").read_text().strip())
        page.locator("#login-form button").click()
        page.locator("#workers .worker-card").first.wait_for()
        assert page.locator("#workers .worker-card").count() == 4
        assert page.locator("#stats .stat").count() == 4
        assert page.locator("#stage-metrics .stage-card").count() == 5
        assert page.locator("#review-mode").input_value() == "automatic"
        assert page.locator("#runtime-version").text_content() == "Boti 0.7.0"
        page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
        for view in ("accounts", "settings", "clips", "campaigns"):
            page.locator(f'[data-view="{view}"]').click()
            page.locator(f"#view-{view}").wait_for(state="visible")
        page.locator("#add-campaign").click()
        page.locator("#campaign-form").wait_for()
        assert page.locator("#c-rate").input_value() == ""
        assert not page.locator("#c-auto").is_checked()
        page.locator("#close-dialog").click()
        page.locator('[data-view="settings"]').click()
        assert int(page.locator("#setting-active-jobs").input_value()) >= 1
        assert int(page.locator("#setting-prefetch").input_value()) >= 1
        assert page.locator("#setting-captions").input_value() in {"plain", "highlight"}
        assert page.locator("#source-language").is_visible()
        # Fixture responses exercise review interactions without creating data in the live main.
        fixture = {
            "id": "ui-fixture",
            "status": "preparing",
            "group_name": "Grupi i provës",
            "start": 0,
            "end": 30,
            "score": 88,
            "publications": [],
            "review_passed": None,
            "metadata": {"title": "Provë e panelit", "verification_reason": "Moment i plotë."},
        }
        page.route("**/api/clips?*", lambda route: route.fulfill(json={"clips": [fixture]}))

        def review(route):
            body = route.request.post_data_json
            fixture.update(review_passed=int(body["passed"]), review_notes=body.get("notes", ""))
            route.fulfill(json={"ok": True})

        page.route("**/api/clips/ui-fixture/review", review)
        page.locator('[data-view="clips"]').click()
        page.locator('[data-review="ui-fixture"][data-passed="false"]').click()
        page.locator("#review-notes").fill("Përmirëso titrat <script>test</script>")
        page.locator("#review-form button").click()
        page.locator(".quality-hold").wait_for()
        assert page.locator(".review-note").text_content() == "Përmirëso titrat <script>test</script>"
        page.locator('[data-review="ui-fixture"][data-passed="true"]').click()
        page.locator(".quality-hold").wait_for(state="detached")
        page.locator('[data-view="accounts"]').click()
        assert page.locator(".account-group").count() >= 12
        page.locator("[data-connect][data-platform=instagram]").first.click()
        page.locator("#connect-token").wait_for(state="visible")
        page.locator("#close-dialog").click()
        page.locator('[data-view="dashboard"]').click()
        # Exercise the complete trial form/report with browser fixtures, without live jobs.
        submitted = []

        def create_trial(route):
            submitted.append(route.request.post_data_json)
            route.fulfill(json={"id": "trial-ui", "created": True})

        page.route("**/api/jobs", create_trial)
        page.route(
            "**/api/jobs/trial-ui/trial-report",
            lambda route: route.fulfill(
                json={
                    "job_id": "trial-ui",
                    "status": "complete",
                    "run_mode": "trial",
                    "error": None,
                    "range": {"start": 60, "seconds": 300},
                    "clip_limit": 3,
                    "elapsed_seconds": 42,
                    "processing_seconds": 30,
                    "readiness": {"ready": True, "reasons": [], "missing_capabilities": []},
                    "counts": {"ready": 1, "skipped": 0, "technical_failed": 0},
                    "note": "Provë pa publikim.",
                    "tasks": [],
                    "clips": [
                        {
                            "id": "trial-clip",
                            "title": "Moment prove <script>bad</script>",
                            "status": "ready",
                            "source_start": 62,
                            "source_end": 92,
                            "score": 90,
                            "version": 1,
                            "selection_reason": "Moment i plotë.",
                            "quality_reason": "Kaloi",
                            "quality_reports": [],
                            "video_url": "/api/clips/trial-clip/video",
                        }
                    ],
                }
            ),
        )
        page.locator("#vod-url").fill("https://twitch.tv/videos/123")
        page.locator("#target-groups input").first.check()
        page.locator("#start-trial").click()
        page.locator("#trial-start").fill("00:01:00")
        page.locator("#trial-limit").select_option("3")
        page.locator("#trial-form button").click()
        page.locator("#trial-report video").wait_for()
        assert submitted[0]["trial"] == {"start_seconds": 60, "duration_seconds": 300, "max_clips": 3}
        assert page.locator("#trial-report video").get_attribute("src") == "/api/clips/trial-clip/video"
        assert "62.0–92.0s" in page.locator("#trial-report").text_content()
        assert page.locator("#trial-report script").count() == 0
        assert "Moment prove <script>bad</script>" in page.locator("#trial-report h3").text_content()
        page.screenshot(path=str(args.output / "trial-report.png"), full_page=True)
        page.locator("#close-dialog").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
    print(
        "Browser check passed: desktop, mobile, stage metrics, settings, reviews, trial form/report and account dialog."
    )


if __name__ == "__main__":
    main()
