"""
安全洞察日报 - 自动生成脚本
每日抓取安全新闻 → LLM 整理 → 生成 Hugo Markdown 日报
"""

import os
import re
import sys
import yaml
import httpx
import feedparser
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
        print(f"    → {len(filtered)} relevant items")
    return domestic_news, intl_news

def build_llm_prompt(domestic_news: list, intl_news: list, today: str) -> str:
    def fmt_items(items, label):
        if not items:
            return f"## {label}\n（暂无相关新闻）\n"
        lines = [f"## {label}"]
        for i, item in enumerate(items[:8], 1):
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
- 按6个板块分类：
  1. 政策合规 (国内政策法规类)
  2. 漏洞威胁 (数据泄露、漏洞事件、攻击)
  3. API安全 (API相关漏洞和动态)
  4. 行业动向 (投融资、报告、趋势)
  5. 开源工具 (安全开源项目)
  6. 社媒分享 (社交媒体上的讨论)
- 每个板块3-5条，每条80-150字
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

    # Extract description from first non-code-block line
    lines = body.split("\n")
    desc_line = ""
    for line in lines:
        stripped = line.strip().strip("`").strip()
        if stripped:
            desc_line = stripped[:150]
            break

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
    print(f"  ✓ Written: {filepath}")
    return filepath

def main():
    today = date.today()
    print(f"=== Security Daily Generator: {today} ===")

    sources = load_sources()
    print("Fetching news...")
    domestic, intl = fetch_all_news(sources)
    print(f"\nTotal: domestic={len(domestic)}, intl={len(intl)}")

    if not domestic and not intl:
        print("[WARN] No news found, skipping generation.")
        return

    # Balance ratio: aim for ~60% domestic
    max_intl = max(3, int(len(domestic) * 0.4 / 0.6))
    if len(intl) > max_intl:
        intl = intl[:max_intl]

    print("Calling LLM...")
    prompt = build_llm_prompt(domestic, intl, today.strftime("%Y/%-m/%-d"))
    body = call_llm(prompt)

    print("Writing report...")
    write_daily_report(body, today)
    print("=== Done ===")

if __name__ == "__main__":
    main()
