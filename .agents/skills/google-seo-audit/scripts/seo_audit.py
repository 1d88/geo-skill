#!/usr/bin/env python3
"""Create repeatable SEO snapshots and compare two audit runs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (compatible; CodexSEOAudit/1.0)"
DEFAULT_URL_LIMIT = 1000


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.robots = ""
        self.h1: list[str] = []
        self.links: list[str] = []
        self.json_ld = 0
        self.capture: str | None = None
        self.buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag in {"title", "h1"}:
            self.capture, self.buffer = tag, []
        if tag == "meta":
            name = values.get("name", "").lower()
            if name == "description":
                self.description = values.get("content", "").strip()
            elif name == "robots":
                self.robots = values.get("content", "").strip()
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonical = values.get("href", "").strip()
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"].strip())
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.json_ld += 1

    def handle_endtag(self, tag: str) -> None:
        if self.capture == tag.lower():
            text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
            if tag.lower() == "title":
                self.title = text
            elif text:
                self.h1.append(text)
            self.capture, self.buffer = None, []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)


def fetch(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return {
                "requested_url": url, "final_url": response.geturl(), "status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "x_robots_tag": response.headers.get("X-Robots-Tag", ""),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "html": body.decode(charset, errors="replace"), "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "requested_url": url, "final_url": exc.geturl(), "status": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "x_robots_tag": exc.headers.get("X-Robots-Tag", ""),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "html": exc.read().decode("utf-8", errors="replace"), "error": str(exc),
        }
    except Exception as exc:
        return {
            "requested_url": url, "final_url": None, "status": None, "content_type": "",
            "x_robots_tag": "", "elapsed_ms": round((time.monotonic() - started) * 1000),
            "html": "", "error": f"{type(exc).__name__}: {exc}",
        }


def parse_html(html: str, base_url: str) -> dict:
    parser = PageParser()
    parser.feed(html)
    host = urllib.parse.urlsplit(base_url).netloc
    internal = []
    for href in parser.links:
        absolute = urllib.parse.urljoin(base_url, href).split("#", 1)[0]
        if urllib.parse.urlsplit(absolute).netloc == host:
            internal.append(absolute)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return {
        "title": parser.title,
        "description": parser.description,
        "canonical": urllib.parse.urljoin(base_url, parser.canonical) if parser.canonical else "",
        "robots_meta": parser.robots,
        "h1": parser.h1,
        "internal_links": sorted(set(internal)),
        "internal_link_count": len(set(internal)),
        "json_ld_blocks": parser.json_ld,
        "text_chars_approx": len(text),
        "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def discover_sitemap(root_url: str, timeout: float, limit: int) -> tuple[list[str], list[str]]:
    queue = [urllib.parse.urljoin(root_url.rstrip("/") + "/", "sitemap.xml")]
    found: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    while queue and len(found) < limit:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        response = fetch(sitemap, timeout)
        if response["status"] != 200:
            errors.append(f"{sitemap}: {response['status'] or response['error']}")
            continue
        try:
            root = ET.fromstring(response["html"])
            locations = [(n.text or "").strip() for n in root.iter() if n.tag.endswith("loc")]
            if root.tag.endswith("sitemapindex"):
                queue.extend(locations)
            else:
                found.extend(locations[: limit - len(found)])
        except ET.ParseError as exc:
            errors.append(f"{sitemap}: invalid XML ({exc})")
    return found, errors


async def render_pages(urls: list[str], timeout: float) -> tuple[dict[str, str], str | None]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {}, "Playwright is not installed; rendered HTML was not collected."
    rendered: dict[str, str] = {}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(user_agent=USER_AGENT)
            for url in urls:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                    rendered[url] = await page.content()
                except Exception:
                    continue
            await browser.close()
    except Exception as exc:
        return rendered, f"Rendering failed: {type(exc).__name__}: {exc}"
    return rendered, None


def make_findings(page: dict) -> list[dict]:
    result = []
    raw = page["raw"]
    if page["status"] != 200:
        result.append({"code": "non_200", "severity": "high", "evidence": page["status"]})
    if "noindex" in (raw["robots_meta"] + " " + page["x_robots_tag"]).lower():
        result.append({"code": "noindex", "severity": "high", "evidence": raw["robots_meta"] or page["x_robots_tag"]})
    for code, value, severity in (
        ("missing_title", raw["title"], "medium"),
        ("missing_h1", raw["h1"], "medium"),
        ("missing_canonical", raw["canonical"], "low"),
    ):
        if not value:
            result.append({"code": code, "severity": severity, "evidence": "empty"})
    if page.get("rendered"):
        differences = {}
        for field in ("title", "h1", "internal_link_count", "text_chars_approx"):
            if page["rendered"][field] != raw[field]:
                differences[field] = {"raw": raw[field], "rendered": page["rendered"][field]}
        page["raw_rendered_differences"] = differences
    return result


def audit(args) -> int:
    urls = list(dict.fromkeys(args.urls))
    sitemap_errors: list[str] = []
    if args.sitemap:
        discovered, sitemap_errors = discover_sitemap(urls[0], args.timeout, args.limit)
        urls = list(dict.fromkeys(urls + discovered))[:args.limit]
    responses = [fetch(url, args.timeout) for url in urls]
    render_map, render_error = ({}, None)
    if args.render:
        render_map, render_error = asyncio.run(render_pages(urls, args.timeout))
    pages = []
    for response in responses:
        html = response.pop("html")
        base = response["final_url"] or response["requested_url"]
        page = {**response, "raw": parse_html(html, base)}
        if response["requested_url"] in render_map:
            page["rendered"] = parse_html(render_map[response["requested_url"]], base)
        page["findings"] = make_findings(page)
        pages.append(page)
    robots = fetch(urllib.parse.urljoin(args.urls[0].rstrip("/") + "/", "robots.txt"), args.timeout)
    robots.pop("html", None)
    snapshot = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": {"seed_urls": args.urls, "used_sitemap": args.sitemap, "limit": args.limit},
        "limitations": ([render_error] if render_error else []) + sitemap_errors,
        "robots_txt": robots,
        "pages": pages,
    }
    Path(args.output).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(pages)} page(s) to {args.output}")
    return 0


def compare(args) -> int:
    before = json.loads(Path(args.before).read_text())
    after = json.loads(Path(args.after).read_text())
    old = {p["requested_url"]: p for p in before["pages"]}
    new = {p["requested_url"]: p for p in after["pages"]}
    changes = []
    for url in sorted(set(old) | set(new)):
        if url not in old or url not in new:
            changes.append({"url": url, "status": "not_tested", "reason": "present in only one snapshot"})
            continue
        old_codes = {f["code"] for f in old[url].get("findings", [])}
        new_codes = {f["code"] for f in new[url].get("findings", [])}
        changes.append({
            "url": url, "fixed": sorted(old_codes - new_codes),
            "remaining": sorted(old_codes & new_codes), "new": sorted(new_codes - old_codes),
            "http_before": old[url]["status"], "http_after": new[url]["status"],
        })
    result = {"schema_version": 1, "before": args.before, "after": args.after, "changes": changes}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote comparison for {len(changes)} URL(s) to {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("audit", help="Create an SEO evidence snapshot")
    run.add_argument("urls", nargs="+", help="Seed URL(s)")
    run.add_argument("--sitemap", action="store_true")
    run.add_argument("--render", action="store_true")
    run.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_URL_LIMIT,
        help=f"Maximum URLs to audit when using a sitemap (default: {DEFAULT_URL_LIMIT})",
    )
    run.add_argument("--timeout", type=float, default=20)
    run.add_argument("--output", required=True)
    run.set_defaults(func=audit)
    diff = commands.add_parser("compare", help="Compare two snapshots")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--output", required=True)
    diff.set_defaults(func=compare)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
