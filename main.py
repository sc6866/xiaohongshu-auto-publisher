from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agents.xiaohongshu_manager import XiaohongshuManager
from common.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xiaohongshu automation CLI")
    parser.add_argument(
        "--settings",
        default=str(Path("config") / "settings.yaml"),
        help="Path to settings file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Run topic scanning and ingestion.")

    produce = subparsers.add_parser("produce", help="Generate one approved content package.")
    produce.add_argument("--topic", default=None, help="Optional topic override.")
    produce_from_images = subparsers.add_parser("produce-from-images", help="Generate one approved content package from one or more images.")
    produce_from_images.add_argument("--images", nargs="+", required=True, help="One or more local image paths.")
    produce_from_images.add_argument("--angle", default=None, help="Optional writing angle, for example 真人测评 or 旅行攻略.")
    produce_from_images.add_argument("--mode", default=None, help="Optional mode override: product_review/travel_guide/lifestyle_note.")
    produce_from_images.add_argument("--style-strength", default="平衡", help="爆款风格强度: 克制 / 平衡 / 强吸引.")

    subparsers.add_parser("publish", help="Publish due queue items in dry-run mode.")
    publish_live = subparsers.add_parser("publish-live", help="Publish one approved content immediately via the real Xiaohongshu MCP service.")
    publish_live.add_argument("--content-id", default=None, help="Optional generated content id.")
    publish_live.add_argument("--visibility", default="private", help="Visibility for the live publish test: private/public/followers.")
    subparsers.add_parser("feedback", help="Run feedback scoring update.")
    subparsers.add_parser("mcp-check", help="Check Xiaohongshu MCP health and login state.")
    sync_latest = subparsers.add_parser("sync-latest", help="Sync latest posts from the bound publisher profile.")
    sync_latest.add_argument("--limit", type=int, default=10, help="Number of recent profile posts to inspect.")

    run_cycle = subparsers.add_parser("run-cycle", help="Run full manager flow once.")
    run_cycle.add_argument("--topic", default=None, help="Optional topic override.")

    scheduler = subparsers.add_parser("scheduler", help="Start APScheduler cron runner.")
    scheduler.add_argument("--once", action="store_true", help="Run each configured scheduler job once in sequence.")
    web = subparsers.add_parser("web", help="Start the local web console.")
    web.add_argument("--host", default=None, help="Bind host for the web console.")
    web.add_argument("--port", type=int, default=None, help="Bind port for the web console.")
    return parser


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings(args.settings)
    manager = XiaohongshuManager(settings)

    if args.command == "scan":
        result = manager.scan_and_ingest()
    elif args.command == "produce":
        result = manager.produce_content(topic=args.topic)
    elif args.command == "produce-from-images":
        result = manager.produce_from_images(
            image_paths=args.images,
            angle=args.angle,
            mode=args.mode,
            style_strength=args.style_strength,
        )
    elif args.command == "publish":
        result = manager.publish_queue()
    elif args.command == "publish-live":
        result = manager.publish_one_live(content_id=args.content_id, visibility=args.visibility)
    elif args.command == "feedback":
        result = manager.run_feedback_loop()
    elif args.command == "mcp-check":
        result = manager.check_mcp_status()
    elif args.command == "sync-latest":
        result = manager.sync_latest_posts(limit=args.limit)
    elif args.command == "run-cycle":
        result = manager.run_full_cycle(topic=args.topic)
    elif args.command == "scheduler":
        from scheduler.cron_runner import run_scheduler, run_scheduler_once

        if args.once:
            result = run_scheduler_once(settings)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        run_scheduler(settings)
        return
    elif args.command == "web":
        from webui.server import run_web_console

        run_web_console(
            settings=settings,
            host=args.host or str(settings.get("web", "host", "127.0.0.1")),
            port=int(args.port or settings.get("web", "port", 8787)),
        )
        return
    else:
        parser.error(f"Unsupported command: {args.command}")
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
