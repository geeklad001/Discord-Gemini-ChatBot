"""Offline CLI to inspect and export asynchronous user feedback reports from SQLite."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import init_runtime_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="匯出 DiscordGeminiBot 非即時使用者回饋與建議清單")
    parser.add_argument("--profile", default="", help="實例設定檔（如 community 對應 .env.community）")
    parser.add_argument("--env", default="", help="指定 .env 檔案路徑")
    parser.add_argument(
        "--status",
        default="open",
        choices=("open", "triaged", "resolved", "wontfix", "all"),
        help="篩選狀態（預設：open）",
    )
    parser.add_argument("--category", default="", help="篩選分類（wiki/bug/feature/ux/other）")
    parser.add_argument("--limit", type=int, default=100, help="最多匯出筆數（預設：100）")
    parser.add_argument("--out", default="", help="寫入指定 Markdown 檔案路徑（省略則輸出至標準輸出）")
    parser.add_argument("--include-reporter", action="store_true", help="於匯出檔內含回報者紀錄（預設隱藏）")
    args = parser.parse_args()

    init_args: list[str] = []
    if args.profile:
        init_args.append(f"--profile={args.profile}")
    if args.env:
        init_args.append(f"--env={args.env}")
    init_runtime_env(argv=init_args)

    from engines import assistant_db

    cat = assistant_db.FEEDBACK_CATEGORY_ALIASES.get(args.category.strip().lower(), args.category.strip().lower()) or None
    reports = assistant_db.list_feedback_reports(status=args.status, category=cat, limit=args.limit)
    md = assistant_db.format_feedback_markdown(
        reports,
        include_reporter=bool(args.include_reporter),
        title=f"使用者回饋與建議清單（status={args.status}）",
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"已匯出 {len(reports)} 筆回饋至 {out_path}")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
