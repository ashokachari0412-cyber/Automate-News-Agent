"""Command Line Interface for the Personal AI Content Agent."""

import argparse
import asyncio
import io
import sys

# Background mode (pythonw / scheduled task) has no console: send output to a log file
if sys.stdout is None or sys.stderr is None:
    from pathlib import Path as _Path
    _log = _Path(__file__).resolve().parent.parent / "artifacts" / "server.log"
    _log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(_log, "a", encoding="utf-8", buffering=1)

# Ensure UTF-8 encoding on Windows to support emojis and international characters
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from config.settings import settings
from src.collectors.factory import get_collector
from src.agents.research_graph import run_research_workflow
from src.notifiers.slack_notifier import SlackNotifier
from src.storage import merge_news

console = Console(legacy_windows=False)


def show_banner():
    banner = Text()
    banner.append("[Google AI] Personal AI Content Agent\n", style="bold cyan")
    banner.append("Strict 24h AI News Detection -> Prioritized Digest -> Image Carousels (Web App)\n", style="bold white")
    banner.append(f"Orchestrator: LangGraph + FastAPI | LLM: Google Gemini ({settings.GEMINI_MODEL})", style="dim")
    console.print(Panel(banner, border_style="cyan", box=box.ROUNDED))


def list_accounts_cmd():
    """Display configured trusted Twitter accounts."""
    accounts = settings.get_trusted_accounts()
    table = Table(
        title=f"Trusted Twitter Accounts for AI News ({len(accounts)} configured)",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Handle", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Category", style="yellow")
    table.add_column("Weight", justify="right", style="green")
    table.add_column("Key Focus / Topics", style="dim")

    for acc in accounts:
        topics_str = ", ".join(acc.topics[:3]) if acc.topics else "AI & Tech"
        table.add_row(
            f"@{acc.handle}",
            acc.name,
            acc.category,
            f"{acc.weight:.1f}x",
            topics_str,
        )

    console.print(table)


async def test_collector_cmd():
    """Test raw tweet collection from the active strategy."""
    collector = get_collector(settings)
    accounts = settings.get_trusted_accounts()

    console.print(f"[bold yellow]Testing collector strategy:[/] [cyan]{type(collector).__name__}[/]")
    console.print(f"Querying {min(len(accounts), 3)} accounts for latest posts (strict 24h window)...")

    tweets = await collector.collect(
        accounts=accounts[:3],
        max_per_account=2,
        max_age_hours=settings.MAX_TWEET_AGE_HOURS
    )

    table = Table(title=f"Collected {len(tweets)} Posts", box=box.ROUNDED)
    table.add_column("Author", style="cyan", no_wrap=True)
    table.add_column("Text Snippet", style="white")
    table.add_column("Engagement", justify="right", style="green")
    table.add_column("Links", style="blue")

    for tw in tweets[:10]:
        table.add_row(
            f"@{tw.author_handle}",
            (tw.text[:75] + "...") if len(tw.text) > 75 else tw.text,
            f"Likes: {tw.likes} | RT: {tw.retweets}",
            str(len(tw.urls)),
        )

    console.print(table)


async def run_pipeline_cmd(send_slack: bool = False):
    """Execute end-to-end AI Research and Prioritization agent."""
    console.print("\n[bold green]>> Scanning for breaking AI news strictly from the last 24 hours...[/]")

    with console.status("[bold cyan]Gathering tweets, checking 24h timestamp, and evaluating with Gemini...", spinner="dots"):
        result = await run_research_workflow()

    # Persist news so the web app can use it
    merge_news(result.items)

    console.print(f"\n[bold green]OK: Pipeline completed in {result.execution_time_seconds}s![/]")
    console.print(
        f"Analyzed [bold cyan]{result.total_tweets_collected}[/] posts -> "
        f"[bold cyan]{result.total_candidates_analyzed}[/] candidate events -> "
        f"[bold green]{len(result.items)}[/] prioritized news stories.\n"
    )

    # Display Clean News Items
    for idx, item in enumerate(result.items, start=1):
        tier_color = "red bold" if item.priority_tier == "HIGH" else "yellow"
        score = item.scores.total

        panel_content = Text()
        panel_content.append("What Happened:\n", style="bold underline")
        panel_content.append(f"{item.summary}\n\n", style="white")

        panel_content.append("Key Takeaways:\n", style="bold underline")
        for bp in item.key_bullet_points:
            panel_content.append(f" - {bp}\n", style="green")

        panel_content.append("\nWhy It Matters:\n", style="bold underline")
        panel_content.append(f"{item.why_it_matters}\n\n", style="dim")


        if item.source_urls:
            panel_content.append(f"\nSource: {item.source_urls[0]}", style="dim blue")

        score_details = (
            f"Impact: {item.scores.impact:.1f}/25 | "
            f"Novelty: {item.scores.novelty:.1f}/25 | "
            f"Credibility: {item.scores.credibility:.1f}/25 | "
            f"Video: {item.scores.video_adaptability:.1f}/25"
        )

        title_header = f"#{idx} [{tier_color}][{item.priority_tier} - {score:.1f}/100][/] {item.title} (@{item.primary_source_handle})"
        console.print(
            Panel(
                panel_content,
                title=title_header,
                subtitle=score_details,
                border_style="cyan" if item.priority_tier == "HIGH" else "dim",
                box=box.ROUNDED,
            )
        )

    # Push to Slack if requested
    if send_slack:
        slack = SlackNotifier()
        if slack.is_configured():
            console.print("[yellow]Dispatching digest to Slack...[/]")
            sent = await slack.send_ranking_digest(result)
            if sent:
                console.print("[green]OK: Successfully sent to Slack![/]")
            else:
                console.print("[red]FAIL: Failed to send to Slack.[/]")
        else:
            console.print("[yellow]Notice: Slack webhook not set in .env. Skipping push.[/]")



def main():
    show_banner()
    parser = argparse.ArgumentParser(description="Personal AI Content Agent CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run news research and print the prioritized digest")
    run_parser.add_argument("--slack", action="store_true", help="Send results to Slack")

    # accounts command
    subparsers.add_parser("accounts", help="List trusted Twitter accounts")

    # test-collector command
    subparsers.add_parser("test-collector", help="Test tweet collector on trusted accounts")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start FastAPI orchestrator server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host address")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port number")
    serve_parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload (always-on mode)")

    # scheduler command
    subparsers.add_parser("scheduler", help="Run autonomous periodic news pipeline scheduler")

    args = parser.parse_args()

    if args.command == "accounts":
        list_accounts_cmd()
    elif args.command == "test-collector":
        asyncio.run(test_collector_cmd())
    elif args.command == "scheduler":
        from src.scheduler import main as run_scheduler
        asyncio.run(run_scheduler())
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("src.api.main:app", host=args.host, port=args.port, reload=not args.no_reload)
    elif args.command == "run" or args.command is None:
        asyncio.run(run_pipeline_cmd(send_slack=getattr(args, "slack", False)))


if __name__ == "__main__":
    main()
