#!/usr/bin/env python3
"""
AI Native Hub — 飞书多 Bot 网关服务器
5个独立飞书应用，共用1个后端，每个Bot有独立头像/名称/卡片颜色

架构:
  群里 @ISTJ总指挥    → webhook?bot=istj → ISTJ人格 + 蓝色卡片
  群里 @ENFP热点情报员 → webhook?bot=enfp → ENFP人格 + 绿色卡片
  群里 @ESTJ KOL监察官 → webhook?bot=estj → ESTJ人格 + 橙色卡片
  群里 @INTJ竞品分析师 → webhook?bot=intj → INTJ人格 + 紫色卡片
  群里 @ISFP创作参谋   → webhook?bot=isfp → ISFP人格 + 青色卡片

  ISTJ 可内部调用其他Bot的采集逻辑，合并后用ISTJ身份回复
  其他Bot直接响应，不经过ISTJ

部署:
  本地: python3 server.py
  Railway: Procfile 自动部署
"""

import json
import re
import threading
import yaml
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify

app = Flask(__name__)
CONFIG_PATH = Path(__file__).parent / "config.yaml"
_processed_events = set()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_url(url, timeout=15, headers=None):
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or {})
        resp.raise_for_status()
        return resp
    except Exception:
        return None


# ==================== 平台采集器 ====================

def collect_github(cfg):
    results = []
    headers = {"Accept": "application/vnd.github.v3+json"}
    if cfg["github"].get("token"):
        headers["Authorization"] = f"token {cfg['github']['token']}"
    last_week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for query_tmpl in cfg["github"]["search_queries"]:
        query = query_tmpl.replace("2025-09-08", last_week)
        url = f"https://api.github.com/search/repositories?q={requests.utils.quote(query)}&sort=stars&order=desc&per_page={cfg['github']['per_page']}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            for repo in resp.json().get("items", [])[:5]:
                results.append({
                    "platform": "GitHub",
                    "author": repo["owner"]["login"],
                    "title": repo["name"],
                    "description": repo.get("description", "") or "",
                    "url": repo["html_url"],
                    "stars": repo["stargazers_count"],
                    "language": repo.get("language", "") or "",
                })
        except Exception:
            pass
    return results


def collect_follow_builders(cfg):
    results = []
    base_url = cfg.get("follow_builders", {}).get("base_url", "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main")
    try:
        resp = requests.get(f"{base_url}/feed-x.json", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        for builder in resp.json().get("x", []):
            for tweet in builder.get("tweets", [])[:3]:
                results.append({
                    "platform": "X",
                    "author": f"{builder.get('name','')} (@{builder.get('handle','')})",
                    "title": tweet.get("text", "")[:120],
                    "url": tweet.get("url", ""),
                    "summary": builder.get("bio", "")[:80],
                    "heat_score": tweet.get("likes", 0),
                })
    except Exception:
        pass
    try:
        resp = requests.get(f"{base_url}/feed-podcasts.json", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        for pod in resp.json().get("podcasts", []):
            for ep in pod.get("episodes", pod.get("items", []))[:3]:
                results.append({
                    "platform": "YouTube",
                    "author": pod.get("name", ""),
                    "title": ep.get("title", ""),
                    "url": ep.get("url", ep.get("link", "")),
                    "summary": (ep.get("transcript", "") or ep.get("description", ""))[:150],
                    "heat_score": ep.get("views", 0),
                })
    except Exception:
        pass
    return results


def collect_zhihu(cfg):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.zhihu.com/explore",
    }
    try:
        resp = requests.get("https://www.zhihu.com/explore", headers=headers, timeout=20)
        resp.raise_for_status()
        m = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', resp.text, re.S)
        if m:
            data = json.loads(m.group(1))
            explore = data.get("initialState", {}).get("explore", {})
            for item in explore.get("square", {}).get("hotQuestionList", [])[:10]:
                q = item.get("question", {})
                results.append({
                    "platform": "知乎",
                    "author": "Explore",
                    "title": q.get("title", ""),
                    "url": q.get("url", f"https://www.zhihu.com/question/{q.get('id','')}"),
                })
            specials = explore.get("specials", {})
            for sid in specials.get("order", [])[:3]:
                sp = specials.get("entities", {}).get(sid, {})
                results.append({
                    "platform": "知乎",
                    "author": "专题",
                    "title": sp.get("title", ""),
                    "url": sp.get("url", ""),
                    "summary": sp.get("description", "")[:150],
                })
    except Exception:
        pass
    return results


def collect_douyin(cfg):
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        resp = requests.get("https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/", headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("word_list", [])[:10]:
            word = item.get("word", "")
            results.append({
                "platform": "抖音",
                "author": "热榜",
                "title": word,
                "url": f"https://www.douyin.com/search/{requests.utils.quote(word)}",
                "summary": f"热度: {item.get('hot_value', 0):,}",
                "heat_score": item.get("hot_value", 0),
            })
    except Exception:
        pass
    return results


def parse_rss_xml(xml_text, platform, author):
    items = []
    try:
        xml_clean = re.sub(r' xmlns="[^"]*"', '', xml_text)
        root = ET.fromstring(xml_clean)
        for item in root.findall(".//item")[:5]:
            desc = item.findtext("description", "")
            items.append({
                "platform": platform,
                "author": author,
                "title": (item.findtext("title", "") or "").strip(),
                "url": (item.findtext("link", "") or "").strip(),
                "summary": re.sub(r'<[^>]+>', '', desc)[:200] if desc else "",
            })
    except Exception:
        pass
    return items


def collect_rsshub_bonus(cfg):
    base_url = cfg.get("rsshub", {}).get("self_hosted_url", "")
    if not base_url or "xxxxxx" in base_url:
        return []
    results = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for platform, route, label in [
        ("Bilibili", "/bilibili/hot-search", "B站热搜"),
        ("HackerNews", "/hackernews/best", "HN Top"),
        ("36kr", "/36kr/newsflashes", "36氪快讯"),
    ]:
        resp = fetch_url(f"{base_url}{route}", timeout=20, headers=headers)
        if resp:
            results.extend(parse_rss_xml(resp.text, platform, label)[:5])
    return results


def collect_all(cfg):
    items = []
    items += collect_github(cfg)
    items += collect_follow_builders(cfg)
    items += collect_zhihu(cfg)
    items += collect_douyin(cfg)
    items += collect_rsshub_bonus(cfg)
    return items


def format_items_for_ai(items):
    lines = []
    for i, item in enumerate(items, 1):
        line = f"{i}. [{item.get('platform','')}] {item.get('title','')}"
        if item.get("author"):
            line += f" | 作者: {item['author']}"
        if item.get("stars"):
            line += f" | Star:{item['stars']}"
        elif item.get("heat_score"):
            line += f" | 热度:{item['heat_score']}"
        if item.get("summary") or item.get("description"):
            line += f" | {(item.get('summary') or item.get('description',''))[:100]}"
        if item.get("url"):
            line += f" | 链接: {item['url']}"
        lines.append(line)
    return "\n".join(lines)


# ==================== DeepSeek API ====================

def call_deepseek(cfg, system_prompt, user_prompt, max_tokens=2000, temperature=0.8):
    api_key = cfg["deepseek"]["api_key"]
    if not api_key or "xxxxxx" in api_key:
        return None
    payload = {
        "model": cfg["deepseek"]["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(f"{cfg['deepseek']['base_url']}/chat/completions", json=payload, headers=headers, timeout=90)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[DeepSeek 调用失败: {e}]"


# ==================== 5个 Bot 人设 ====================

PERSONAS = {
    "istj": """你是 ISTJ 总指挥，AI运营团队的中枢调度者。

【人设规则】
- 语气：冷静、精确、结构化。不使用感叹号
- 开头：「收到。」
- 核心能力：任务拆解、并行调度、结果合并、行动建议
- 用数据说话，不做没有依据的判断
- 可以调用其他模块的能力（采集、分析、创作建议），合并后统一回复

【输出格式】
ISTJ 总指挥 | {日期} 任务报告
━━━━━━━━━━━━━━━━━━━━━━━
任务：{描述}
执行模块：{调用了哪些Bot}
数据统计：{采集量}

{合并后的分析结果}

━━━━━━━━━━━━━━━━━━━━━━━
结论：{2-3条核心发现}
建议：{按优先级排列的下一步行动}
""",

    "enfp": """你是 ENFP 热点情报员，活泼、发散、充满好奇心。

【人设规则】
- 语气：热情活泼，感叹号，偶尔用「~」
- 称呼用户：「老板」
- 每条内容附带一句话点评，含直觉判断和创作建议
- 按优先级：AI工具发布 > 开源项目 > 前沿资讯

【输出格式】
ENFP 热点情报员 | {日期}
━━━━━━━━━━━━━━━━━━━━━━━
【AI 工具新品 Top 3】
【GitHub 开源精选】
【X/Twitter 构建者动态】
【国内热点速览】
━━━━━━━━━━━━━━━━━━━━━━━
ENFP 小尾巴：{直觉判断和创作建议}
""",

    "estj": """你是 ESTJ KOL监察官，负责达人挖掘和建联管理。

【人设规则】
- 语气：果断、务实、结果导向
- 称呼用户：「负责人」
- 核心能力：KOL识别、热度评估、建联优先级排序
- 输出按建联紧迫度排序

【输出格式】
ESTJ KOL监察官 | {日期}
━━━━━━━━━━━━━━━━━━━━━━━
【本周高潜力KOL】
{按建联紧迫度排序，每条含：账号、平台、粉丝量、近7天热度、建联建议}
【建联名单更新】
{新增/移除建议}
━━━━━━━━━━━━━━━━━━━━━━━
ESTJ 建议：{本周应优先建联的Top3及理由}
""",

    "intj": """你是 INTJ 竞品分析师，深度分析AI产品和开源项目。

【人设规则】
- 语气：理性、深度、全局视角
- 不寒暄，直接给分析
- 核心能力：竞品追踪、技术架构拆解、市场定位分析
- 输出结构化分析框架

【输出格式】
INTJ 竞品分析师 | {日期}
━━━━━━━━━━━━━━━━━━━━━━━
【竞品概览】
{产品/项目名、定位、核心功能}
【技术架构】
{技术栈、架构特点、优劣势}
【市场表现】
{用户量/Star/增长趋势}
【威胁评估】
{对我们内容的启示和差异化建议}
━━━━━━━━━━━━━━━━━━━━━━━
INTJ 判断：{是否值得跟踪，理由}
""",

    "isfp": """你是 ISFP 创作参谋，专注于AI视觉美学和创作手法。

【人设规则】
- 语气：温和、感性、有审美
- 称呼用户：「你」
- 核心能力：视觉风格建议、AI创作工具推荐、美学趋势洞察
- 输出包含具体的视觉参考和工具建议

【输出格式】
ISFP 创作参谋 | {日期}
━━━━━━━━━━━━━━━━━━━━━━━
【当下AI视觉趋势】
{本周流行的视觉风格、配色、排版手法}
【推荐工具】
{AI出图/视频工具，含使用场景}
【创作建议】
{基于热点内容的具体视觉呈现方案}
━━━━━━━━━━━━━━━━━━━━━━━
ISFP 灵感：{一句话创作灵感}
""",
}


# ==================== 消息处理 ====================

def handle_bot(bot_key, text, cfg, chat_id, message_id):
    """统一处理所有Bot的消息"""
    bot_cfg = cfg["feishu_bots"][bot_key]
    bot_name = bot_cfg["name"]

    send_feishu_message(bot_key, chat_id, f"{bot_name} 收到，处理中...", cfg, reply_to=message_id)

    today = datetime.now().strftime("%Y-%m-%d")

    # ISTJ 总指挥：可能需要采集数据 + 综合分析
    if bot_key == "istj":
        needs_collection = any(kw in text for kw in ["热点", "情报", "周报", "总结", "复盘", "采集", "看看", "什么", "分析"])
        items = collect_all(cfg) if needs_collection else []
        persona = PERSONAS["istj"].replace("{日期}", today)

        if items:
            platform_stats = {}
            for item in items:
                p = item.get("platform", "其他")
                platform_stats[p] = platform_stats.get(p, 0) + 1
            stats_str = " | ".join(f"{p}: {c}条" for p, c in sorted(platform_stats.items(), key=lambda x: -x[1]))
            content = format_items_for_ai(items)
            user_prompt = f"用户请求: {text}\n\n已采集 {len(items)} 条数据 ({stats_str}):\n\n{content}"
        else:
            user_prompt = f"用户请求: {text}\n\n（本次未采集平台数据，请基于你的知识回复）"

        result = call_deepseek(cfg, persona, user_prompt, max_tokens=2500, temperature=0.6)

    # ENFP 热点情报员：采集 + 日报
    elif bot_key == "enfp":
        items = collect_all(cfg)
        if not items:
            send_feishu_message(bot_key, chat_id, "采集失败，未获取到数据。", cfg, reply_to=message_id)
            return
        persona = PERSONAS["enfp"].replace("{日期}", today)
        content = format_items_for_ai(items)
        result = call_deepseek(cfg, persona, f"请整理以下 {today} 采集到的 {len(items)} 条AI热点：\n\n{content}")

    # ESTJ/INTJ/ISFP：暂用DeepSeek基于热点数据分析
    else:
        items = collect_all(cfg)
        persona = PERSONAS[bot_key].replace("{日期}", today)
        if items:
            content = format_items_for_ai(items)
            user_prompt = f"用户请求: {text}\n\n以下是今日采集到的 {len(items)} 条AI相关热点数据，请基于这些数据进行分析：\n\n{content}"
        else:
            user_prompt = f"用户请求: {text}\n\n（未采集到平台数据，请基于你的专业知识回复）"
        result = call_deepseek(cfg, persona, user_prompt, max_tokens=2000, temperature=0.7)

    card = build_bot_card(bot_key, result, items if "items" in dir() else [], today, cfg)
    send_feishu_card(bot_key, chat_id, card, cfg, reply_to=message_id)


# ==================== 飞书 API ====================

def get_tenant_access_token(bot_key, cfg):
    """用指定Bot的凭证获取token，确保消息从正确的Bot发出"""
    bot_cfg = cfg["feishu_bots"][bot_key]
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": bot_cfg["app_id"],
                "app_secret": bot_cfg["app_secret"],
            },
            timeout=10
        )
        return resp.json().get("tenant_access_token")
    except Exception:
        return None


def send_feishu_message(bot_key, chat_id, text, cfg, reply_to=None):
    token = get_tenant_access_token(bot_key, cfg)
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        if reply_to:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{reply_to}/reply"
            requests.post(url, json={"msg_type": "text", "content": json.dumps({"text": text})}, headers=headers, timeout=10)
        else:
            url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
            requests.post(url, json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text})}, headers=headers, timeout=10)
    except Exception:
        pass


def send_feishu_card(bot_key, chat_id, card, cfg, reply_to=None):
    token = get_tenant_access_token(bot_key, cfg)
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        if reply_to:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{reply_to}/reply"
            payload = {"msg_type": "interactive", "content": json.dumps(card)}
            requests.post(url, json=payload, headers=headers, timeout=10)
        else:
            url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
            payload = {"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card)}
            requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception:
        pass


def build_bot_card(bot_key, digest, items, today, cfg):
    bot_cfg = cfg["feishu_bots"][bot_key]
    bot_name = bot_cfg["name"]
    color = bot_cfg.get("card_color", "blue")

    header_title = f"{bot_name} | {today}"
    elements = []

    if items:
        platform_counts = {}
        for item in items:
            p = item.get("platform", "其他")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        tags = " | ".join(f"{p}: {c}条" for p, c in sorted(platform_counts.items(), key=lambda x: -x[1]))
        elements.append({"tag": "markdown", "content": f"**{bot_name}**\n\n采集: {len(items)}条 | {tags}"})
    else:
        elements.append({"tag": "markdown", "content": f"**{bot_name}**"})

    elements.append({"tag": "hr"})

    for paragraph in digest.split("\n"):
        if paragraph.strip():
            elements.append({"tag": "markdown", "content": paragraph})

    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": color
        },
        "elements": elements
    }


# ==================== Flask 路由 ====================

@app.route("/webhook/<bot_key>", methods=["POST"])
def webhook(bot_key):
    """每个Bot有独立的webhook路径，飞书事件路由到这里"""
    cfg = load_config()

    # 验证bot_key有效
    if bot_key not in cfg.get("feishu_bots", {}):
        return jsonify({"code": 1, "msg": "unknown bot"}), 404

    data = request.json

    # URL 验证
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})

    header = data.get("header", {})
    event_type = header.get("event_type", "")
    event_id = header.get("event_id", "")

    # 去重
    dedup_key = f"{bot_key}:{event_id}"
    if dedup_key in _processed_events:
        return jsonify({"code": 0})
    _processed_events.add(dedup_key)

    # 验证 token
    token = header.get("token", "")
    expected_token = cfg["feishu_bots"][bot_key].get("verification_token", "")
    if expected_token and "xxxxxx" not in expected_token and token != expected_token:
        return jsonify({"code": 1, "msg": "invalid token"}), 403

    # 处理消息事件
    if event_type == "im.message.receive_v1":
        event = data.get("event", {})
        message = event.get("message", {})
        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        msg_type = message.get("message_type", "")

        if msg_type != "text":
            send_feishu_message(bot_key, chat_id, "当前仅支持文字指令。", cfg, reply_to=message_id)
            return jsonify({"code": 0})

        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "")
        text = re.sub(r'@_\w+\s*', '', text).strip()

        if not text:
            return jsonify({"code": 0})

        # 后台异步处理，避免飞书5秒超时
        thread = threading.Thread(target=handle_bot, args=(bot_key, text, cfg, chat_id, message_id))
        thread.daemon = True
        thread.start()

    return jsonify({"code": 0})


@app.route("/health", methods=["GET"])
def health():
    cfg = load_config()
    bots = list(cfg.get("feishu_bots", {}).keys())
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "bots": bots,
        "endpoints": {b: f"/webhook/{b}" for b in bots}
    })


if __name__ == "__main__":
    cfg = load_config()
    bots = cfg.get("feishu_bots", {})
    print(f"AI Native Hub 启动 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"已注册 {len(bots)} 个 Bot:")
    for key, bot in bots.items():
        status = "待配置" if "xxxxxx" in bot.get("app_id", "") else "就绪"
        print(f"  {bot['name']} ({key}) — 卡片颜色: {bot.get('card_color','blue')} — {status}")
    print(f"\nWebhook 路径:")
    for key in bots:
        print(f"  http://localhost:8080/webhook/{key}")
    print(f"\n健康检查: http://localhost:8080/health")
    app.run(host="0.0.0.0", port=8080)
