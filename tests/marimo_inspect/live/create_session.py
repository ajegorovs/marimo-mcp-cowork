"""Create a session in a running marimo server.

Usage:
    uv run python tests/marimo_inspect/live/create_session.py [url]

Creates a session by accessing the notebook URL via Playwright (headless browser).
The server must be running with --no-token --headless.

After running this, the live tests will find the session.
"""

from __future__ import annotations

import asyncio
import sys

import httpx2 as httpx

from marimo_inspection.discovery import discover_servers


async def main(url: str | None = None):
    """Create a session in the running marimo server."""
    # Discover the server
    servers = await discover_servers()
    if not servers:
        print("No marimo server found. Start one first:")
        print("  uv run marimo edit notebooks/test_marimo.py --no-token --headless")
        sys.exit(1)

    server_url = url or servers[0].url
    print(f"Using server: {server_url}")

    # Check for existing sessions
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{server_url}/api/sessions")
        sessions = resp.json()
        if sessions:
            print(f"Session already exists: {list(sessions.keys())}")
            return

    # Use Playwright to access the notebook URL and create a session
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # Use Firefox which is more likely to be installed
        try:
            browser = await p.firefox.launch()
        except Exception:  # noqa: BLE001 - broad fallback across browsers
            # Fallback to chromium
            browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            # Access the server URL to create a session
            print(f"Accessing {server_url}/edit/notebooks/test_marimo.py...")
            await page.goto(
                f"{server_url}/edit/notebooks/test_marimo.py", wait_until="networkidle"
            )
            print("Session created successfully!")
        except Exception as e:  # noqa: BLE001 - report any goto failure
            print(f"Error creating session: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(url))
