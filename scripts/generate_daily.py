"""
安全洞察日报 - 自动生成脚本
每日抓取安全新闻 → LLM 整理 → 生成 Hugo Markdown 日报
"""

import os
import re
import sys
import random
import yaml
import httpx
import feedparser
from collections import defaultdict
from datetime import datetime, date, timezone
from pathlib import Path
from openai import OpenAI

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "cn"
SOURCES_FILE = Path(__file__).resolve().parent / "news_sources.yaml"

KEYWORDS = [
    "数据安全", "数据泄露", "数据出境", "个人信息", "隐私",
    "API安全", "API漏洞", "API", "接口安全",
    "网络安全", "漏洞", "合规", "安全法规",
    "data breach", "data leak", "data security",
    "API security", "API vulnerability", "API breach",
    "cybersecurity", "privacy", "compliance",
    "ransomware", "hack", "exploit", "CVE",
]

DOMESTIC_SITES = [
    "secrss.com", "cac.gov.cn", "freebuf.com",
    "ndata.gov.cn", "4hou.com", "anquan.co",
    "shushuosecurity.com", "kanxue.com",
]

def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def fetch_rss(url: str, max_items: int = 15) -> list[dict]:
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", entry.get("description", ""))
            published = entry.get("published", "")
            items.append({
                "title": title,
                "link": link,
                "summary": summary[:500],
                "published": published,
            })
    except Exception as e:
        print(f"  [WARN] RSS fetch failed: {url} - {e}")
    return items

def is_relevant(item: dict) -> bool:
    text = f"{item['title']} {item['summary']}".lower()
    return any(kw.lower() in text for kw in KEYWORDS)

def classify_source(url: str) -> str:
    for site in DOMESTIC_SITES:
        if site in url:
            return "domestic"
    return "international"

def fetch_all_news(sources: dict) -> tuple[list[dict], list[dict]]:
    domestic_news, intl_news = [], []
    all_sources = (
        [("domestic", s) for s in sources["domestic"]] +
        [("international", s) for s in sources["international"]]
    )
    for region, src in all_sources:
        print(f"  Fetching [{region}] {src['name']}...")
        items = fetch_rss(src["url"])
        filtered = [i for i in items if is_relevant(i)]
        for item in filtered:
            item["region"] = region
            item["source"] = src["name"]
        if region == "domestic":
            domestic_news.extend(filtered)
        else:
            intl_news.extend(filtered)
        print(f"    \u2192 {len(filtered)} relevant items")
    return domestic_news, intl_news

def sample_per_source(items: list, max_per_source: int = 3) -> list:
    groups = defaultdict(list)
    for item in items:
        groups[item["source"]].append(item)
    sampled = []
    for src, src_items in groups.items():
        sampled.extend(src_items[:max_per_source])
    random.shuffle(sampled)
    return sampled

def build_llm_prompt(domestic_news: list, intl_news: list, today: str) -> str:
    def fmt_items(items, label):
        if not items:
            return f"## {label}\n（暂无相关新闻）\n"
        sampled = sample_per_source(items, max_per_source=3)
        lines = [f"## {label}"]
        for i, item in enumerate(sampled, 1):
            lines.append(
                f"{i}. [{item['title']}]({item['link']})\n"
                f"   来源: {item['source']} | {item['summary'][:200]}"
            )
        return "\n".join(lines)

    return f"""你是一个数据安全和API安全领域的日报编辑。请根据以下原始新闻素材，生成一份中文安全日报。

要求：
- 日期: {today}
- 语言: 简体中文
- 风格: 类似科技日报，用 emoji 点缀，每条新闻带链接
- 国内内容约占60%，国外约占40%
- 尽量覆盖不同来源，避免集中在同一网站
- 按6个板块分类：
  1. 政策合规 (国内政策法规类)
  2. 漏洞威胁 (数据泄露、漏洞事件、攻击)
  3. API安全 (API相关漏洞和动态)
  4. 行业动向 (投融资、报告、趋势)
  5. 开源工具 (安全开源项目)
  6. 社媒分享 (社交媒体上的讨论)
- 每个板块2-3条，每条80-150字
- 今日摘要放在开头的 ``` 代码块中，正文不要用代码块包裹
- 最终输出为完整的 Markdown 格式（不含front matter，只输出body部分，不要将正文放入代码块）

原始新闻素材：

{fmt_items(domestic_news, "国内新闻")}

{fmt_items(intl_news, "国际新闻")}
"""

def call_llm(prompt: str) -> str:
    token = os.environ.get("DEEPSEEK_API_KEY")
    endpoint = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not token:
        print("[ERROR] DEEPSEEK_API_KEY not set")
        sys.exit(1)

    client = OpenAI(
        api_key=token,
        base_url=endpoint,
    )
    resp = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=16384,
    )
    if resp.choices[0].finish_reason == "length":
        print("[ERROR] LLM response truncated")
        sys.exit(1)
    return resp.choices[0].message.content

def ensure_month_dir(d: date) -> Path:
    month_dir = CONTENT_DIR / d.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    index_file = month_dir / "_index.md"
    if not index_file.exists():
        index_file.write_text(
            f"---\ntitle: {d.strftime('%Y年%m月')}\n"
            f"linkTitle: {d.strftime('%Y年%m月')}\n"
            f"cascade:\n  type: docs\n---\n\n"
            f"{{{{< hextra/hero-headline >}}}}\n"
            f"{d.strftime('%Y年%m月')} 安全日报归档\n"
            f"{{{{< /hextra/hero-headline >}}}}\n"
        )
    return month_dir

def write_daily_report(body: str, today: date):
    month_dir = ensure_month_dir(today)
    filename = today.strftime("%Y-%m-%d.md")
    filepath = month_dir / filename

    # Strip outer code block if LLM wrapped entire output
    lines = body.split("\n")
    if len(lines) > 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        body = "\n".join(lines[1:-1])

    # Extract description from first meaningful line
    lines = body.split("\n")
    desc_line = ""
    for line in lines:
        stripped = line.strip().strip("`").strip()
        if stripped:
            desc_line = stripped[:150]
            break
    # Escape double quotes for YAML double-quoted string
    desc_line = desc_line.replace('"', '\\"')

    front_matter = f"""---
linkTitle: {today.strftime('%m-%d')} 安全日报
title: 安全洞察日报 {today.strftime('%Y/%-m/%-d')}
weight: 1
breadcrumbs: false
comments: true
description: "{desc_line}"
---
"""
    content = front_matter + body
    filepath.write_text(content, encoding="utf-8")
    print(f"  \u2713 Written: {filepath}")
    return filepath

def get_latest_daily_reports(limit: int = 6) -> list[dict]:
    """Collect the latest daily reports across monthly dirs."""
    reports = []
    for month_dir in sorted(CONTENT_DIR.iterdir(), reverse=True):
        if not month_dir.is_dir() or not re.match(r"\d{4}-\d{2}", month_dir.name):
            continue
        for f in sorted(month_dir.glob("*-*-*.md"), reverse=True):
            if f.name == "_index.md":
                continue
            content = f.read_text(encoding="utf-8")
            # parse front matter
            m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
            if not m:
                continue
            fm = yaml.safe_load(m.group(1))
            # extract first meaningful text line as subtitle
            body = content[m.end():].strip()
            first_line = ""
            for line in body.split("\n"):
                stripped = line.strip().strip("`").strip()
                if stripped and not stripped.startswith("---"):
                    first_line = stripped[:150]
                    break
            reports.append({
                "path": f"/news/{month_dir.name}/{f.stem}",
                "title": fm.get("title", f.stem),
                "subtitle": first_line,
            })
            if len(reports) >= limit:
                return reports
    return reports


def update_homepage_cards(reports: list[dict]):
    """Update the latest-6 cards and hero badge link in the homepage _index.md."""
    index_path = CONTENT_DIR / "_index.md"
    content = index_path.read_text(encoding="utf-8")

    # Update hero badge "阅读今日日报" link to point to the latest report
    if reports:
        latest = reports[0]
        content = re.sub(
            r'(hextra/hero-badge link=")/news/[^"]*("[^>]*>.*?阅读今日日报.*?{{\s*< /\s*hextra/hero-badge\s*>\s*}})',
            rf'\1{latest["path"]}\2',
            content,
        )

    # Update latest-6 cards section
    cards = []
    for r in reports:
        icon = random.choice(["shield-exclamation", "eye", "bell", "academic-cap", "document-text", "chart-bar"])
        cards.append(
            f'{{{{< card link="{r["path"]}" title="{r["title"]}" '
            f'subtitle="{r["subtitle"]}" icon="{icon}" >}}}}'
        )
    card_block = "\n".join(cards)

    new_content = re.sub(
        r"<!-- LATEST_6_CARDS_START -->.*?<!-- LATEST_6_CARDS_END -->",
        f"<!-- LATEST_6_CARDS_START -->\n{card_block}\n<!-- LATEST_6_CARDS_END -->",
        content,
        flags=re.DOTALL,
    )
    if new_content != content:
        index_path.write_text(new_content, encoding="utf-8")
        print(f"  \u2713 Updated homepage latest cards ({len(reports)} reports)")


def main():
    today = date.today()
    print(f"=== Security Daily Generator: {today} ===")

    sources = load_sources()
    print("Fetching news...")
    domestic, intl = fetch_all_news(sources)
    print(f"\nTotal: domestic={len(domestic)}, intl={len(intl)}")

    # Always update homepage cards with latest reports
    reports = get_latest_daily_reports(limit=6)
    update_homepage_cards(reports)

    if not domestic and not intl:
        print("[WARN] No news found, skipping generation.")
        return

    # Balance ratio: per-source sampling preserves diversity
    intl = sample_per_source(intl, max_per_source=3)

    print("Calling LLM...")
    prompt = build_llm_prompt(domestic, intl, today.strftime("%Y/%-m/%-d"))
    body = call_llm(prompt)

    print("Writing report...")
    write_daily_report(body, today)
    print("=== Done ===")

if __name__ == "__main__":
    main()
