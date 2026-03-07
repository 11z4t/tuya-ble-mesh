#!/usr/bin/env python3
"""Dual AI code review — Claude + GPT on every push.

Runs both reviewers in parallel, posts combined report to Slack.
Triggered by CI pipeline or manually.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx


def get_key(op_ref: str) -> str:
    """Read secret from 1Password via safe-op.sh."""
    result = subprocess.run(
        [os.path.expanduser("~/scripts/secret-helpers/safe-op.sh"), "read", op_ref],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"safe-op failed: {result.stderr.strip()}")
    tmpfile = result.stdout.strip()
    try:
        val = Path(tmpfile).read_text().strip()
        lines = [line.strip() for line in val.splitlines() if line.strip()]
        for line in reversed(lines):
            if len(line) >= 30:
                return line
        return lines[-1] if lines else val
    finally:
        Path(tmpfile).unlink(missing_ok=True)


def get_diff() -> str:
    """Get the diff of the last commit."""
    result = subprocess.run(
        ["git", "log", "-1", "--patch", "--stat"],
        capture_output=True,
        text=True,
        cwd=os.environ.get("REPO_PATH", "."),
    )
    diff = result.stdout
    if len(diff) > 40000:
        diff = diff[:40000] + "\n... (truncated)"
    return diff


REVIEW_PROMPT = """You are a senior code reviewer for a Home Assistant BLE Mesh integration.
Rate this commit 1-10 on: Quality, Security, Documentation, Maintainability, HA Best Practices.
Give an overall score out of 100.
List max 5 specific issues (file:line if possible).
Compare to Shelly (HA's best Platinum integration).
Be concise — max 300 words.

COMMIT DIFF:
{diff}"""


async def review_gpt(client: httpx.AsyncClient, api_key: str, diff: str) -> dict:
    """Run GPT-4o-mini review."""
    t0 = time.time()
    resp = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": REVIEW_PROMPT.format(diff=diff)}],
            "max_tokens": 1024,
            "temperature": 0.2,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return {
        "reviewer": "GPT-4o-mini",
        "elapsed_s": round(time.time() - t0, 1),
        "review": resp.json()["choices"][0]["message"]["content"],
    }


async def review_claude(client: httpx.AsyncClient, api_key: str, diff: str) -> dict:
    """Run Claude Sonnet review."""
    t0 = time.time()
    resp = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": REVIEW_PROMPT.format(diff=diff)}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"] if data.get("content") else "No response"
    return {
        "reviewer": "Claude Sonnet",
        "elapsed_s": round(time.time() - t0, 1),
        "review": text,
    }


def post_to_slack(webhook_url: str, commit_sha: str, results: list[dict]) -> None:
    """Post combined review to Slack."""
    blocks = [f"*Dual AI Review — `{commit_sha[:8]}`*\n"]
    for r in results:
        blocks.append(f"*{r['reviewer']}* ({r['elapsed_s']}s):\n{r['review']}\n")

    text = "\n---\n".join(blocks)
    if len(text) > 3000:
        text = text[:3000] + "\n... (truncated)"

    httpx.post(webhook_url, json={"text": text}, timeout=10)


async def main() -> None:
    diff = get_diff()
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=os.environ.get("REPO_PATH", "."),
    ).stdout.strip()

    print(f"Reviewing commit {commit_sha[:8]} ({len(diff)} chars)")

    # CI uses env vars, local uses 1Password
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not openai_key:
        openai_key = get_key("op://TALA/OpenAI API Key/credential")
    if not claude_key:
        claude_key = get_key("op://Platform/Anthropic API Key/credential")

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            review_gpt(client, openai_key, diff),
            review_claude(client, claude_key, diff),
            return_exceptions=True,
        )

    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append({"reviewer": "ERROR", "elapsed_s": 0, "review": str(r)})
        else:
            final.append(r)

    for r in final:
        print(f"\n{'=' * 60}")
        print(f"{r['reviewer']} ({r['elapsed_s']}s)")
        print(f"{'=' * 60}")
        print(r["review"])

    # Post to Slack if webhook available
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        post_to_slack(slack_url, commit_sha, final)
        print("\nPosted to Slack")

    # Save to file
    output = Path("/tmp/dual-review-latest.md")
    with output.open("w") as f:
        for r in final:
            f.write(f"\n# {r['reviewer']} ({r['elapsed_s']}s)\n\n{r['review']}\n\n---\n")
    print(f"\nSaved to {output}")


if __name__ == "__main__":
    asyncio.run(main())
