"""Automated News Pipeline Scheduler (Containerized Service).

Periodically triggers strict 24h AI news scanning, ranking, and Telegram card dispatch
during configured active hours.
"""

import asyncio
from datetime import datetime
import logging
import signal
import sys
from typing import Optional

from rich.console import Console

from config.settings import settings
from src.agents.research_graph import run_research_workflow
from src.telegram.bot_service import bot_service
from src.telegram.notifier import TelegramNotifier

logger = logging.getLogger("pipeline_scheduler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
console = Console(legacy_windows=False)


class PipelineScheduler:
    """Autonomous scheduler for 24h AI news scanning & Telegram dispatch."""

    def __init__(self):
        self.notifier = TelegramNotifier()
        self.is_running = False
        self.interval_seconds = max(settings.SCHEDULE_INTERVAL_HOURS, 1) * 3600
        self.run_on_startup = settings.SCHEDULE_RUN_ON_STARTUP

    def is_within_active_hours(self) -> bool:
        """Check if current time is within user's configured working hours."""
        if not settings.ACTIVE_HOURS_ENABLED:
            return True
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(getattr(settings, "TIMEZONE", "Asia/Kolkata"))
            curr_hour = datetime.now(tz).hour
        except Exception:
            curr_hour = datetime.now().hour
        return settings.ACTIVE_HOURS_START <= curr_hour < settings.ACTIVE_HOURS_END

    async def execute_scan(self, reason: str = "scheduled"):
        """Execute one full end-to-end news research & dispatch cycle."""
        console.print(f"\n[bold green]🔄 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Triggering News Scan ({reason})...[/]")
        
        if not self.is_within_active_hours():
            console.print(
                f"[yellow]🌙 Current hour ({datetime.now().hour}:00) is outside active hours "
                f"({settings.ACTIVE_HOURS_START}:00 - {settings.ACTIVE_HOURS_END}:00). Skipping notification.[/]"
            )
            return

        try:
            result = await run_research_workflow()
            console.print(
                f"[green]✅ Scan completed. Analyzed {result.total_tweets_collected} posts. "
                f"Found {len(result.items)} prioritized news items.[/]"
            )

            # Cache items so Telegram Bot container can access them via shared volume
            bot_service.cache_news(result.items)

            # Send clean news cards to Telegram
            if self.notifier.is_configured() and result.items:
                console.print(f"[cyan]🚀 Dispatching {len(result.items)} news updates to Telegram...[/]")
                sent = await self.notifier.send_ranking_digest(result)
                if sent:
                    console.print("[bold green]✅ News digest successfully sent to Telegram![/]")
                else:
                    console.print("[red]❌ Failed to send digest to Telegram.[/]")
            else:
                console.print("[dim]Telegram not configured or no items found.[/]")

        except Exception as e:
            logger.error(f"Error during scheduled scan: {e}", exc_info=True)

    async def start(self):
        """Start the periodic scheduling loop."""
        self.is_running = True
        console.print("[bold cyan]====================================================[/]")
        console.print("[bold cyan]🚀 Personal AI News Agent — Autonomous Pipeline Scheduler[/]")
        console.print(f"[white]• Interval:[/] [yellow]{settings.SCHEDULE_INTERVAL_HOURS} hours ({self.interval_seconds}s)[/]")
        console.print(f"[white]• Active Hours:[/] [yellow]{settings.ACTIVE_HOURS_START}:00 - {settings.ACTIVE_HOURS_END}:00[/]")
        console.print(f"[white]• Run on startup:[/] [yellow]{self.run_on_startup}[/]")
        console.print("[bold cyan]====================================================[/]\n")

        # Run on startup if configured
        if self.run_on_startup:
            await self.execute_scan(reason="startup")

        # Scheduling loop
        while self.is_running:
            try:
                next_run_time = datetime.fromtimestamp(datetime.now().timestamp() + self.interval_seconds)
                console.print(f"[dim]⏳ Next automated scan scheduled at: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')}[/]")
                
                # Sleep in short increments to respond quickly to termination signals
                elapsed = 0
                while elapsed < self.interval_seconds and self.is_running:
                    step = min(10, self.interval_seconds - elapsed)
                    await asyncio.sleep(step)
                    elapsed += step

                if self.is_running:
                    await self.execute_scan(reason="interval")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(30)

        console.print("[yellow]Scheduler stopped cleanly.[/]")

    def stop(self):
        """Signal scheduler to stop."""
        self.is_running = False


async def main():
    scheduler = PipelineScheduler()

    def handle_signal():
        console.print("\n[yellow]Shutdown signal received. Stopping scheduler...[/]")
        scheduler.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows does not support loop.add_signal_handler for all signals
            pass

    try:
        await scheduler.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())
