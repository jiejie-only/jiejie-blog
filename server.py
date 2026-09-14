#!/usr/bin/env python3
"""杰杰的博客整站服务：公开静态页 + /admin 登录写作。

用法：
  python server.py
  默认 http://127.0.0.1:8080
  管理入口 http://127.0.0.1:8080/admin/login

首次运行会生成 data/config.json 与管理员口令（打印在终端）。
生产环境建议：环境变量 JIEJIE_HOST=0.0.0.0，前面挂 Nginx + HTTPS。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
POSTS_DIR = ROOT / "posts"
PINS_PATH = DATA / "pins.json"
ADMIN_DIR = ROOT / "admin"
UPLOADS_DIR = ROOT / "assets" / "uploads"
CONFIG_PATH = DATA / "config.json"
HOST = os.environ.get("JIEJIE_HOST", "127.0.0.1")
PORT = int(os.environ.get("JIEJIE_PORT", "8080"))

TAGS = ("随笔", "技术", "安全", "读书", "生活")  # 默认建议标签
TAG_MAX_LEN = 16
PBKDF2_ITERS = 260_000
SESSION_TTL_HOURS = 12



def sanitize_url(url: str, allow_data_image: bool = False) -> str:
    """过滤 javascript:/vbscript:/危险 data: 等，返回安全 URL 或 #。"""
    u = (url or "").strip()
    if not u:
        return "#"
    u2 = re.sub(r"[\s\x00-\x1f]+", "", u)
    low = u2.lower()
    if low.startswith("data:"):
        if allow_data_image and re.match(
            r"^data:image/(png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$",
            low,
        ):
            return u2
        return "#"
    if low.startswith(("javascript:", "vbscript:", "file:", "blob:")):
        return "#"
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) and not low.startswith(
        ("http:", "https:", "mailto:")
    ):
        return "#"
    return u


def normalize_tag(raw: str) -> str:
    """允许自定义标签；去掉危险字符，限制长度。"""
    tag = re.sub(r"\s+", " ", (raw or "").strip())
    tag = re.sub(r'[<>&"\'`\\/]', "", tag)
    tag = tag.strip(".,;:，。；：")
    if not tag:
        return "随笔"
    return tag[:TAG_MAX_LEN]


def _strip_front_matter(md: str) -> str:
    text = md or ""
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return text
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :])
    return text


def _is_junk_token(s: str) -> bool:
    """过滤 base64 垃圾串、无中文的长乱码。"""
    compact = re.sub(r"\s+", "", s)
    if len(compact) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return True
    if len(compact) >= 50 and not re.search(r"[一-鿿]", compact):
        if " " not in compact[:30] and not re.search(r"[。！？.!?]", compact):
            return True
    return False


def strip_markdown(md: str) -> str:
    text = _strip_front_matter(md or "")
    text = re.sub(r"(?s)```.*?```", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*`>_~]+", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _score_excerpt_para(p: str) -> int:
    score = 0
    if re.search(r"[。！？；]", p):
        score += 4
    if re.search(r"[一-鿿]{6,}", p):
        score += 2
    if len(p) >= 30:
        score += 1
    if len(p) >= 80:
        score += 1
    urls = len(re.findall(r"https?://\S+", p))
    if urls:
        score -= urls * 3
    if re.match(r"^\s*https?://", p):
        score -= 6
    if _is_junk_token(p):
        score -= 10
    return score


def generate_excerpt(md: str, limit: int = 78) -> str:
    """提取像人话的摘要：跳过标题/代码/链接堆，优先完整句子。"""
    text = _strip_front_matter(md or "")
    text = re.sub(r"(?s)```.*?```", "\n", text)
    text = re.sub(r"(?s)~~~.*?~~~", "\n", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "\n", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)

    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        lines: list[str] = []
        skip = False
        for line in block.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#") or s.startswith("```") or s.startswith("~~~"):
                skip = True
                break
            if s.startswith("|") and s.endswith("|"):
                skip = True
                break
            s = re.sub(r"^\s*[-*+]\s+", "", s)
            s = re.sub(r"^\s*\d+[.、)]\s+", "", s)
            s = re.sub(r"^>\s*", "", s)
            s = re.sub(r"[*`_~]+", "", s).strip()
            if not s or _is_junk_token(s):
                continue
            if re.match(r"^https?://\S+$", s):
                continue
            # 「短文字 + 链接」的书签行不当摘要
            if re.search(r"https?://", s):
                non_url = re.sub(r"https?://\S+", "", s)
                non_url = re.sub(r"[*`_~]+", "", non_url).strip()
                if len(non_url) < 16:
                    continue
            lines.append(s)
        if skip or not lines:
            continue
        para = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if len(para) < 20:
            continue
        # 纯 IP / CIDR / 端口 之类不当摘要
        if re.fullmatch(r"[\d.:/a-fA-Fx]+", para):
            continue
        if not re.search(r"[一-鿿A-Za-z]{4,}", para):
            continue
        paragraphs.append(para)

    if not paragraphs:
        # 兜底：逐行找第一句像正文的中文
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("```"):
                continue
            s = re.sub(r"^\s*[-*+]\s+", "", s)
            s = re.sub(r"^\s*\d+[.、)]\s+", "", s)
            s = re.sub(r"[*`_~]+", "", s).strip()
            if len(s) < 16 or _is_junk_token(s):
                continue
            if re.fullmatch(r"[\d.:/a-fA-Fx]+", s):
                continue
            if re.match(r"^https?://", s):
                continue
            if re.search(r"https?://", s) and len(re.sub(r"https?://\S+", "", s).strip()) < 16:
                continue
            if len(s) <= limit:
                return s
            window = s[:limit]
            pos = max(window.rfind("。"), window.rfind("！"), window.rfind("？"))
            if pos >= 12:
                return window[: pos + 1]
            return window.rstrip("，, ") + "…"
        # 最后兜底：编号步骤标题
        steps: list[str] = []
        for m in re.finditer(r"(?m)^\s*\d+[、.]\s*(.+)$", text):
            t = re.sub(r"[*`_~]+", "", m.group(1)).strip()
            if re.search(r"[一-鿿]{2,}", t) and 2 <= len(t) <= 24:
                steps.append(t)
        if steps:
            joined = "；".join(steps[:3])
            return joined[:limit]
        return ""

    candidates = paragraphs[:8]
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-_score_excerpt_para(pair[1]), pair[0]),
    )
    chosen = ranked[0][1]
    if len(chosen) < 28 and len(ranked) > 1 and _score_excerpt_para(ranked[1][1]) >= 2:
        chosen = f"{chosen} {ranked[1][1]}"

    chosen = re.sub(r"\s+", " ", chosen).strip()
    if _is_junk_token(chosen):
        return ""
    if len(chosen) <= limit:
        return chosen

    window = chosen[:limit]
    best = -1
    for sep in ("。", "！", "？", "；", "…", ".", "!", "?", ";"):
        pos = window.rfind(sep)
        if pos > best:
            best = pos
    if best >= 18:
        return window[: best + 1]
    for sep in ("，", ",", "、"):
        pos = window.rfind(sep)
        if pos >= int(limit * 0.5):
            return window[:pos].rstrip() + "…"
    return window.rstrip("，,、 ") + "…"


def collect_tags() -> list[str]:
    """默认标签 + 站内已用标签，供写作台下拉。"""
    used: list[str] = []
    if POSTS_DIR.exists():
        for md_path in POSTS_DIR.glob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            m = re.search(r"(?m)^tag:\s*(.+)$", text)
            if m:
                t = normalize_tag(m.group(1))
                if t and t not in used:
                    used.append(t)
    ordered: list[str] = []
    for t in list(TAGS) + used:
        t = normalize_tag(t)
        if t and t not in ordered:
            ordered.append(t)
    return ordered

_lock = threading.Lock()
_sessions: dict[str, dict] = {}
_login_attempts: dict[str, list[float]] = {}


# ---------- 配置与口令 ----------

def ensure_config() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if "password_hash" not in cfg:
            raise SystemExit("config.json 缺少 password_hash，请删除后重新初始化")
        return cfg

    username = os.environ.get("JIEJIE_USER", "admin")
    password = os.environ.get("JIEJIE_PASS") or secrets.token_urlsafe(12)
    salt = secrets.token_bytes(16)
    cfg = {
        "username": username,
        "password_hash": hash_password(password, salt),
        "salt": salt.hex(),
        "secret": secrets.token_urlsafe(32),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except Exception:
        pass
    print("=" * 52)
    print("  首次初始化完成，请妥善保存管理员口令")
    print(f"  用户名: {username}")
    print(f"  密码:   {password}")
    print(f"  配置:   {CONFIG_PATH}")
    print("  修改密码：删除 data/config.json 后重启，或改环境变量 JIEJIE_PASS")
    print("=" * 52)
    return cfg


def hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return dk.hex()


def verify_password(password: str, cfg: dict) -> bool:
    salt = bytes.fromhex(cfg["salt"])
    expect = cfg["password_hash"]
    got = hash_password(password, salt)
    return hmac.compare_digest(got, expect)


def sign(value: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def unsign(signed: str, secret: str) -> str | None:
    if not signed or "." not in signed:
        return None
    value, mac = signed.rsplit(".", 1)
    expect = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(mac, expect):
        return value
    return None


def create_session(cfg: dict) -> str:
    sid = secrets.token_urlsafe(24)
    csrf = secrets.token_urlsafe(24)
    exp = time.time() + SESSION_TTL_HOURS * 3600
    with _lock:
        _sessions[sid] = {"csrf": csrf, "exp": exp, "user": cfg["username"]}
    return sign(f"{sid}|{csrf}", cfg["secret"])


def load_session(cookie_val: str | None, cfg: dict) -> dict | None:
    if not cookie_val:
        return None
    raw = unsign(cookie_val, cfg["secret"])
    if not raw or "|" not in raw:
        return None
    sid, csrf = raw.split("|", 1)
    with _lock:
        sess = _sessions.get(sid)
        if not sess:
            return None
        if sess["exp"] < time.time():
            _sessions.pop(sid, None)
            return None
        if not hmac.compare_digest(sess["csrf"], csrf):
            return None
        return sess


def destroy_session(cookie_val: str | None, cfg: dict) -> None:
    if not cookie_val:
        return
    raw = unsign(cookie_val, cfg["secret"])
    if not raw or "|" not in raw:
        return
    sid = raw.split("|", 1)[0]
    with _lock:
        _sessions.pop(sid, None)


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    window = 300
    with _lock:
        arr = [t for t in _login_attempts.get(ip, []) if now - t < window]
        _login_attempts[ip] = arr
        if len(arr) >= 8:
            return False
        arr.append(now)
        return True


# ---------- Markdown ----------

def slugify(title: str) -> str:
    """只保留中英文、数字与连字符；下划线等非法字符自动清理。"""
    s = (title or "").strip().lower()
    s = re.sub(r"[^0-9a-z一-鿿]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return f"post-{date.today().isoformat()}"
    s = s[:48].strip("-")
    return s or f"post-{date.today().isoformat()}"


def estimate_minutes(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return max(1, round((cjk / 400) + (words / 220)))


def build_toc(md: str) -> tuple[str, list[tuple[str, str]]]:
    items: list[tuple[str, str]] = []
    for i, line in enumerate(md.splitlines()):
        if line.startswith("## "):
            title = line[3:].strip()
            sid = slugify(title) or f"h-{i}"
            items.append((sid, title))
    if not items:
        return "", []
    lis = "\n".join(
        f'          <li><a href="#{sid}">{html.escape(t)}</a></li>' for sid, t in items
    )
    return (
        f"""
      <aside class="toc" aria-label="本页目录">
        <p class="toc-title">On this page</p>
        <ul class="toc-list">
{lis}
        </ul>
      </aside>""",
        items,
    )


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s or "|" not in s:
        return False
    return bool(re.match(r"^\|?[\s:|-]+\|?$", s)) and "-" in s


def _split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # 不转义的简单切分（支持 \| 转义）
    parts = re.split(r"(?<!\\)\|", s)
    return [p.replace("\\|", "|").strip() for p in parts]


def _parse_alignment(sep_line: str) -> list[str]:
    aligns = []
    for cell in _split_table_row(sep_line):
        cell = cell.strip()
        if cell.startswith(":") and cell.endswith(":"):
            aligns.append("center")
        elif cell.endswith(":"):
            aligns.append("right")
        elif cell.startswith(":"):
            aligns.append("left")
        else:
            aligns.append("")
    return aligns


def md_to_html(md: str, headings: list[tuple[str, str]]) -> str:
    """简易 GFM：表格、任务列表、删除线、围栏代码、引用、列表、标题。"""
    heading_ids = {title: sid for sid, title in headings}
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    code_fence = ""
    code_lang = ""
    code_buf: list[str] = []
    in_ul = in_ol = in_block = in_table = False
    table_aligns: list[str] = []
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            text = " ".join(para).strip()
            if text:
                out.append(f"<p>{inline(text)}</p>")
            para = []

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_block():
        nonlocal in_block
        if in_block:
            out.append("</blockquote>")
            in_block = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False

    def close_all():
        flush_para()
        close_lists()
        close_block()
        close_table()

    def inline(text: str) -> str:
        text = html.escape(text, quote=False)

        def _img(m):
            alt, src = m.group(1), m.group(2)
            safe = html.escape(sanitize_url(src, allow_data_image=True), quote=True)
            # alt 由正文拼进属性位置，必须转义引号与尖括号，
            # 否则 `alt="x" onerror="alert(1)"` 这类可注入任意属性。
            safe_alt = html.escape(re.sub(r"[<>]", "", alt), quote=True)
            return '<img src="%s" alt="%s">' % (safe, safe_alt)

        def _a(m):
            label, href = m.group(1), m.group(2)
            safe = html.escape(sanitize_url(href), quote=True)
            rel = ' rel="noopener noreferrer"'
            if safe.lower().startswith("http"):
                rel += ' target="_blank"'
            return '<a href="%s"%s>%s</a>' % (safe, rel, label)

        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _img, text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"(?<![*\w])_([^_]+)_(?![*\w])", r"<em>\1</em>", text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _a, text)
        return text

    def render_list_item(content: str) -> str:
        m = re.match(r"^\[([ xX])\]\s*(.*)$", content.strip())
        if m:
            checked = m.group(1).lower() == "x"
            mark = " checked" if checked else ""
            label = inline(m.group(2))
            return (
                f'<li class="task-item">'
                f'<input type="checkbox" disabled{mark}> {label}</li>'
            )
        return f"<li>{inline(content)}</li>"

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 围栏代码
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            if not in_code:
                close_all()
                in_code = True
                code_fence = fence
                code_lang = stripped[3:].strip()
                code_buf = []
            elif fence == code_fence or stripped.startswith(code_fence):
                cls = f' class="language-{html.escape(code_lang)}"' if code_lang else ""
                out.append(
                    f"<pre><code{cls}>" + html.escape("\n".join(code_buf)) + "</code></pre>"
                )
                in_code = False
                code_fence = ""
                code_lang = ""
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # 空行
        if not stripped:
            # 表格中空行结束表格；引用中空行若下一行仍是 > 则保持
            if in_table:
                close_table()
            if in_block:
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if not nxt.lstrip().startswith(">"):
                    close_block()
            flush_para()
            close_lists()
            i += 1
            continue

        # 水平线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            close_all()
            out.append("<hr>")
            i += 1
            continue

        # 表格：当前行含 | 且下一行是分隔行
        if (
            "|" in stripped
            and i + 1 < len(lines)
            and _is_table_sep(lines[i + 1])
        ):
            close_all()
            header_cells = _split_table_row(stripped)
            table_aligns = _parse_alignment(lines[i + 1])
            out.append('<div class="table-wrap"><table><thead><tr>')
            for idx, cell in enumerate(header_cells):
                style = ""
                if idx < len(table_aligns) and table_aligns[idx]:
                    style = f' style="text-align:{table_aligns[idx]}"'
                out.append(f"<th{style}>{inline(cell)}</th>")
            out.append("</tr></thead><tbody>")
            in_table = True
            i += 2
            continue

        # 表体
        if in_table and "|" in stripped and not stripped.startswith("#"):
            cells = _split_table_row(stripped)
            out.append("<tr>")
            for idx, cell in enumerate(cells):
                style = ""
                if idx < len(table_aligns) and table_aligns[idx]:
                    style = f' style="text-align:{table_aligns[idx]}"'
                out.append(f"<td{style}>{inline(cell)}</td>")
            out.append("</tr>")
            i += 1
            continue
        if in_table:
            close_table()

        # 标题
        m_h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m_h:
            close_all()
            level = len(m_h.group(1))
            title = m_h.group(2).strip()
            if level == 1:
                sid = heading_ids.get(title, slugify(title) or "top")
                out.append(f'<h2 id="{sid}">{inline(title)}</h2>')
            elif level == 2:
                sid = heading_ids.get(title, slugify(title))
                out.append(f'<h2 id="{sid}">{inline(title)}</h2>')
            elif level == 3:
                out.append(f"<h3>{inline(title)}</h3>")
            else:
                out.append(f"<h4>{inline(title)}</h4>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            flush_para()
            close_lists()
            close_table()
            if not in_block:
                out.append("<blockquote>")
                in_block = True
            body = re.sub(r"^>\s?", "", stripped)
            if body.strip():
                out.append(f"<p>{inline(body.strip())}</p>")
            i += 1
            continue

        # 无序 / 任务列表
        m_ul = re.match(r"^([-*+])\s+(.*)$", stripped)
        if m_ul:
            flush_para()
            close_block()
            close_table()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(render_list_item(m_ul.group(2)))
            i += 1
            continue

        # 有序列表
        m_ol = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m_ol:
            flush_para()
            close_block()
            close_table()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(render_list_item(m_ol.group(1)))
            i += 1
            continue

        close_block()
        close_table()
        para.append(stripped)
        i += 1

    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    close_all()
    return "\n          ".join(out)


POST_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{description}">
  <meta name="color-scheme" content="light dark">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>{title} · 杰杰的博客</title>
  <link rel="icon" href="../assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="../assets/css/style.css">
  <script src="../assets/js/main.js" defer></script>
</head>
<body>
  <div class="progress" id="progress" aria-hidden="true"></div>
  <header class="site-header" id="site-header">
    <div class="header-inner">
      <a class="brand" href="../index.html">
        <svg class="brand-seal" viewBox="0 0 64 64" aria-hidden="true">
          <rect width="64" height="64" rx="12" fill="#C4512B"/>
          <rect x="5" y="5" width="54" height="54" rx="8" fill="none" stroke="#F3EFE6" stroke-width="2.5" opacity="0.9"/>
          <text x="32" y="41" text-anchor="middle" font-family="Georgia, serif" font-size="26" font-weight="700" fill="#F3EFE6">杰</text>
        </svg>
        <span class="brand-text">
          <span class="brand-name">杰杰的博客</span>
          <span class="brand-tag">Personal Notes</span>
        </span>
      </a>
      <button class="nav-toggle" id="nav-toggle" type="button" aria-controls="site-nav" aria-expanded="false" aria-label="打开导航菜单">
        <span></span><span></span><span></span>
      </button>
      <nav class="nav" id="site-nav" aria-label="主导航">
        <a class="nav-link" href="../index.html">首页</a>
        <a class="nav-link" href="../posts.html">文章</a>
        <a class="nav-link" href="../about.html">关于</a>
        <button class="theme-btn" id="theme-toggle" type="button" aria-label="切换主题" aria-pressed="false">
          <svg data-icon="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" hidden>
            <circle cx="12" cy="12" r="4"/>
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>
          </svg>
          <svg data-icon="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
            <path d="M21 14.5A8.5 8.5 0 0 1 9.5 3 7 7 0 1 0 21 14.5z"/>
          </svg>
        </button>
      </nav>
    </div>
  </header>
  <main>
    <div class="article-layout">
      <article>
        <header class="article-header">
          <p class="eyebrow">{tag_label} · {tag}</p>
          <h1 class="article-title">{title}</h1>
          <div class="article-meta">
            <time datetime="{date_iso}">{date_iso}</time>
            <span class="meta-mono">约 {minutes} 分钟</span>
            <span class="tag">{tag}</span>
          </div>
        </header>
        <div class="prose">
          {body}
        </div>
        <footer class="article-footer">
          <a class="btn btn-ghost" href="../posts.html">返回目录</a>
        </footer>
      </article>
{toc}
    </div>
  </main>
  <footer class="site-footer">
    <div class="footer-inner">
      <p>© 2026 杰杰的博客 · 用静态页认真写字</p>
      <div class="footer-links">
        <a href="../posts.html">文章</a>
        <a href="../about.html">关于</a>
      </div>
    </div>
  </footer>
</body>
</html>
"""

ITEM_POSTS = """      <li class="post-item{pin_cls}" data-tags="{tag}">
        <time class="post-date" datetime="{date_iso}">{date_iso}</time>
        <div class="post-main">
          <h2 class="post-title">{pin_badge}<a href="posts/{slug}.html">{title}</a></h2>
          <p class="post-excerpt">{excerpt}</p>
        </div>
        <div class="post-meta"><span class="tag">{tag}</span><span>约 {minutes} 分钟</span></div>
      </li>
"""

ITEM_HOME = """        <li class="post-item{pin_cls}">
          <time class="post-date" datetime="{date_iso}">{date_iso}</time>
          <div class="post-main">
            <h3 class="post-title">{pin_badge}<a href="posts/{slug}.html">{title}</a></h3>
            <p class="post-excerpt">{excerpt}</p>
          </div>
          <div class="post-meta"><span class="tag">{tag}</span></div>
        </li>
"""


def load_pins() -> list[str]:
    if not PINS_PATH.exists():
        return []
    try:
        data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    return []


def save_pins(pins: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PINS_PATH.write_text(json.dumps(pins, ensure_ascii=False, indent=2), encoding="utf-8")


def set_pinned(slug: str, pinned: bool) -> list[str]:
    pins = load_pins()
    if pinned:
        if slug not in pins:
            pins.insert(0, slug)
    else:
        pins = [s for s in pins if s != slug]
    save_pins(pins)
    return pins


def render_list_item(p: dict, for_home: bool = False) -> str:
    pinned = bool(p.get("pinned"))
    pin_badge = '<span class="pin-badge">置顶</span>' if pinned else ""
    pin_cls = " is-pinned" if pinned else ""
    tpl = ITEM_HOME if for_home else ITEM_POSTS
    kwargs = dict(
        pin_cls=pin_cls,
        pin_badge=pin_badge,
        tag=html.escape(p.get("tag") or "随笔"),
        date_iso=html.escape(p.get("date") or ""),
        slug=html.escape(p.get("slug") or ""),
        title=html.escape(p.get("title") or ""),
        excerpt=html.escape((p.get("excerpt") or p.get("title") or "")[:120]),
        minutes=p.get("minutes") or 1,
    )
    needed = set(re.findall(r"\{(\w+)\}", tpl))
    return tpl.format(**{k: kwargs[k] for k in needed})


FEATURE_CARD = """        <a class="feature-card{featured_cls}" href="posts/{slug}.html">
          <div>
            <p class="feature-kicker">置顶 · {tag}</p>
            <h3 class="feature-title">{title}</h3>
            <p class="feature-desc">{excerpt}</p>
          </div>
          <div class="feature-foot">
            <span>{date_iso}</span>
            <span>约 {minutes} 分钟</span>
          </div>
        </a>
"""


def render_feature_card(p: dict, is_primary: bool = False) -> str:
    return FEATURE_CARD.format(
        featured_cls=" featured" if is_primary else "",
        slug=html.escape(p.get("slug") or ""),
        tag=html.escape(p.get("tag") or "随笔"),
        title=html.escape(p.get("title") or ""),
        excerpt=html.escape((p.get("excerpt") or p.get("title") or "")[:100]),
        date_iso=html.escape(p.get("date") or ""),
        minutes=p.get("minutes") or 1,
    )


def rebuild_site_lists() -> None:
    """置顶 → 首页精选；最新文章仅非置顶；posts.html 全量（置顶在前）。"""
    posts = list_posts()
    pinned = [p for p in posts if p.get("pinned")]
    rest = [p for p in posts if not p.get("pinned")]

    # posts.html：全部，置顶在前
    path = ROOT / "posts.html"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = '<ul class="post-list" style="margin-top: 1rem;">'
        start = text.find(marker)
        if start < 0:
            marker = '<ul class="post-list">'
            start = text.find(marker)
        if start >= 0:
            start_end = text.find(">", start) + 1
            end = text.find("</ul>", start_end)
            if end > start_end:
                items = "".join(render_list_item(p, False) + "\n" for p in posts)
                text = text[:start_end] + "\n" + items + text[end:]
                path.write_text(text, encoding="utf-8")

    path = ROOT / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")

    # 首页侧栏统计：文章数、起笔年
    count = len(posts)
    years = []
    for p in posts:
        d = p.get("date") or ""
        if len(d) >= 4 and d[:4].isdigit():
            years.append(int(d[:4]))
    year = min(years) if years else date.today().year
    text = re.sub(
        r'(<b id="stat-count">)\d+(</b>)',
        rf"\g<1>{count}\g<2>",
        text,
        count=1,
    )
    text = re.sub(
        r'(<b id="stat-year">)\d{4}(</b>)',
        rf"\g<1>{year}\g<2>",
        text,
        count=1,
    )
    # 兼容旧写法（无 id 时按「文章」旁数字）
    if 'id="stat-count"' not in text:
        text = re.sub(
            r'(<b>)\d+(</b><span>文章</span>)',
            rf"\g<1>{count}\g<2>",
            text,
            count=1,
        )

    # 精选：置顶文章卡片
    feat_sec = text.find('id="featured-title"')
    grid_tag = '<div class="feature-grid">'
    grid = text.find(grid_tag, feat_sec if feat_sec >= 0 else 0)
    if grid >= 0:
        cards = "".join(render_feature_card(p, i == 0) for i, p in enumerate(pinned))
        if not cards:
            cards = (
                '        <a class="feature-card" href="posts.html" style="grid-column:1/-1">'
                "<div><p class=\"feature-kicker\">精选</p>"
                "<h3 class=\"feature-title\">还没有置顶文章</h3>"
                "<p class=\"feature-desc\">在写作台「已发布」里点「置顶」，文章会出现在这里。</p></div>"
                '<div class="feature-foot"><span>提示</span><span>后台操作</span></div></a>\n'
            )
        open_end = grid + len(grid_tag)
        i = open_end
        depth = 1
        close_at = -1
        while i < len(text) and depth > 0:
            nxt_open = text.find("<div", i)
            nxt_close = text.find("</div>", i)
            if nxt_close < 0:
                break
            if nxt_open != -1 and nxt_open < nxt_close:
                depth += 1
                i = nxt_open + 4
            else:
                depth -= 1
                if depth == 0:
                    close_at = nxt_close
                    break
                i = nxt_close + 6
        if close_at > open_end:
            text = text[:open_end] + "\n" + cards + "      " + text[close_at:]

    # 最新文章：仅非置顶
    section = text.find('id="latest-title"')
    if section >= 0:
        ul = text.find('<ul class="post-list">', section)
        if ul >= 0:
            start_end = text.find(">", ul) + 1
            end = text.find("</ul>", start_end)
            if end > start_end:
                items = "".join(render_list_item(p, True) + "\n" for p in rest[:12])
                text = text[:start_end] + "\n" + items + text[end:]

    path.write_text(text, encoding="utf-8")


def insert_into_posts_html(item_html: str) -> None:
    path = ROOT / "posts.html"
    text = path.read_text(encoding="utf-8")
    # 覆盖同 slug 旧条目
    m = re.search(
        r'href="posts/([^"]+)\.html"', item_html
    )
    if m:
        slug = re.escape(m.group(1))
        text = re.sub(
            rf'[ \t]*<li class="post-item"[^>]*>[\s\S]*?href="posts/{slug}\.html"[\s\S]*?</li>\n',
            "",
            text,
            count=1,
        )
    marker = '<ul class="post-list" style="margin-top: 1rem;">'
    idx = text.find(marker)
    if idx < 0:
        marker = '<ul class="post-list">'
        idx = text.find(marker)
    if idx < 0:
        raise RuntimeError("posts.html 中找不到 post-list")
    insert_at = text.find("\n", idx) + 1
    path.write_text(text[:insert_at] + item_html + text[insert_at:], encoding="utf-8")


def insert_into_index_html(item_html: str) -> None:
    path = ROOT / "index.html"
    text = path.read_text(encoding="utf-8")
    m = re.search(r'href="posts/([^"]+)\.html"', item_html)
    if m:
        slug = re.escape(m.group(1))
        text = re.sub(
            rf'[ \t]*<li class="post-item">[\s\S]*?href="posts/{slug}\.html"[\s\S]*?</li>\n',
            "",
            text,
            count=1,
        )
    section = text.find('id="latest-title"')
    if section < 0:
        return
    idx = text.find('<ul class="post-list">', section)
    if idx < 0:
        return
    insert_at = text.find("\n", idx) + 1
    path.write_text(text[:insert_at] + item_html + text[insert_at:], encoding="utf-8")


def save_post(payload: dict) -> dict:
    title = (payload.get("title") or "").strip()
    tag = normalize_tag(payload.get("tag") or "随笔")
    date_iso = (payload.get("date") or date.today().isoformat()).strip()
    excerpt = (payload.get("excerpt") or "").strip()
    md = payload.get("content") or ""
    md = _strip_front_matter(md)
    raw_slug = (payload.get("slug") or "").strip()
    slug = slugify(raw_slug or title)

    if not title:
        raise ValueError("标题不能为空")
    if len(md) > 800_000:
        raise ValueError("正文过长（上限约 800KB）")
    if len(title) > 120:
        title = title[:120]
    if not excerpt:
        excerpt = generate_excerpt(md)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_iso):
        date_iso = date.today().isoformat()

    toc_html, headings = build_toc(md)
    body = md_to_html(md, headings)
    minutes = estimate_minutes(md + excerpt)
    description = (excerpt or f"{title} — 杰杰的博客").replace('"', "'")

    html_page = POST_TEMPLATE.format(
        title=html.escape(title),
        description=html.escape(description),
        tag=html.escape(tag),
        tag_label="Note",
        date_iso=html.escape(date_iso),
        minutes=minutes,
        body=body,
        toc=toc_html,
    )

    post_path = POSTS_DIR / f"{slug}.html"
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    post_path.write_text(html_page, encoding="utf-8")
    (POSTS_DIR / f"{slug}.md").write_text(
        f"---\ntitle: {title}\ndate: {date_iso}\ntag: {tag}\nexcerpt: {excerpt}\n---\n\n{md}\n",
        encoding="utf-8",
    )

    with _lock:
        rebuild_site_lists()

    return {
        "ok": True,
        "slug": slug,
        "path": f"posts/{slug}.html",
        "url": f"/posts/{slug}.html",
        "minutes": minutes,
    }


SLUG_RE = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff][0-9A-Za-z\u4e00-\u9fff_-]{0,79}$")


def _valid_slug(slug: str) -> str:
    """严格校验 slug：只允许字母/数字/中文/下划线/连字符。

    禁止 . / \\ 等路径字符，从根上杜绝 `../index` 形式的路径穿越。
    """
    slug = (slug or "").strip()
    if SLUG_RE.match(slug):
        return slug
    cleaned = slugify(slug) if slug else ""
    if SLUG_RE.match(cleaned):
        return cleaned
    raise ValueError("slug 无效")


def parse_front_matter(md_text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    body = md_text
    if md_text.startswith("---"):
        parts = md_text.split("\n", 2)
        # parts[0]='---', parts[1]=fm, rest after second ---
        if len(parts) >= 3 and "---" in parts[1]:
            # malformed; try split properly
            pass
        lines = md_text.splitlines()
        if lines and lines[0].strip() == "---":
            i = 1
            fm_lines = []
            while i < len(lines) and lines[i].strip() != "---":
                fm_lines.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip() == "---":
                for line in fm_lines:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip().lower()] = v.strip()
                body = "\n".join(lines[i + 1 :]).lstrip("\n")
    return meta, body


def list_posts() -> list[dict]:
    items: list[dict] = []
    if not POSTS_DIR.exists():
        return items
    pin_order = load_pins()
    pin_set = set(pin_order)
    for html_path in POSTS_DIR.glob("*.html"):
        slug = html_path.stem
        md_path = POSTS_DIR / f"{slug}.md"
        title = tag = date_iso = excerpt = ""
        content = ""
        if md_path.exists():
            raw = md_path.read_text(encoding="utf-8")
            meta, content = parse_front_matter(raw)
            title = meta.get("title") or slug
            tag = meta.get("tag") or "随笔"
            date_iso = meta.get("date") or ""
            excerpt = meta.get("excerpt") or ""
        else:
            text = html_path.read_text(encoding="utf-8")
            m = re.search(r"<title>(.*?)·\s*杰杰的博客</title>", text, re.S)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
            m = re.search(r'<time datetime="([^"]+)">', text)
            if m:
                date_iso = m.group(1)
            m = re.search(r'<span class="tag">([^<]+)</span>', text)
            if m:
                tag = m.group(1).strip()
            m = re.search(r'name="description" content="([^"]*)"', text)
            if m:
                excerpt = m.group(1)
        items.append(
            {
                "slug": slug,
                "title": title or slug,
                "tag": normalize_tag(tag),
                "date": date_iso,
                "excerpt": excerpt,
                "has_md": md_path.exists(),
                "url": f"/posts/{slug}.html",
                "minutes": estimate_minutes(content or excerpt or title or slug),
                "pinned": slug in pin_set,
            }
        )
    pinned = [x for x in items if x.get("pinned")]
    rest = [x for x in items if not x.get("pinned")]
    pinned.sort(key=lambda x: pin_order.index(x["slug"]) if x["slug"] in pin_order else 999)
    rest.sort(key=lambda x: (x.get("date") or "", x["slug"]), reverse=True)
    return pinned + rest


def load_post(slug: str) -> dict:
    slug = _valid_slug(slug)
    md_path = POSTS_DIR / f"{slug}.md"
    html_path = POSTS_DIR / f"{slug}.html"
    if not html_path.exists() and not md_path.exists():
        raise FileNotFoundError("文章不存在")

    title = tag = date_iso = excerpt = ""
    content = ""
    if md_path.exists():
        raw = md_path.read_text(encoding="utf-8")
        meta, content = parse_front_matter(raw)
        title = meta.get("title") or slug
        tag = meta.get("tag") or "随笔"
        date_iso = meta.get("date") or date.today().isoformat()
        excerpt = meta.get("excerpt") or ""
    elif html_path.exists():
        text = html_path.read_text(encoding="utf-8")
        m = re.search(r"<title>(.*?)·\s*杰杰的博客</title>", text, re.S)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else slug
        m = re.search(r'<time datetime="([^"]+)">', text)
        date_iso = m.group(1) if m else date.today().isoformat()
        m = re.search(r'<span class="tag">([^<]+)</span>', text)
        tag = m.group(1).strip() if m else "随笔"
        m = re.search(r'name="description" content="([^"]*)"', text)
        excerpt = m.group(1) if m else ""
        # 无 md 时正文无法完整还原，提示用户从 HTML 粘贴
        content = ""

    if not tag:
        tag = "随笔"
    tag = normalize_tag(tag)
    return {
        "slug": slug,
        "title": title,
        "tag": tag,
        "date": date_iso,
        "excerpt": excerpt,
        "content": content,
        "has_md": md_path.exists(),
        "url": f"/posts/{slug}.html",
    }


def remove_list_entries(slug: str) -> None:
    slug_re = re.escape(slug)
    for name, pattern in (
        (
            "posts.html",
            rf'[ \t]*<li class="post-item"[^>]*>[\s\S]*?href="posts/{slug_re}\.html"[\s\S]*?</li>\n',
        ),
        (
            "index.html",
            rf'[ \t]*<li class="post-item">[\s\S]*?href="posts/{slug_re}\.html"[\s\S]*?</li>\n',
        ),
    ):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new = re.sub(pattern, "", text, count=1)
        if new != text:
            path.write_text(new, encoding="utf-8")


def delete_post(slug: str) -> dict:
    slug = _valid_slug(slug)
    html_path = POSTS_DIR / f"{slug}.html"
    md_path = POSTS_DIR / f"{slug}.md"
    if not html_path.exists() and not md_path.exists():
        raise FileNotFoundError("文章不存在")
    with _lock:
        if html_path.exists():
            html_path.unlink()
        if md_path.exists():
            md_path.unlink()
        pins = [s for s in load_pins() if s != slug]
        save_pins(pins)
        rebuild_site_lists()
    return {"ok": True, "slug": slug}


def save_post_with_rename(payload: dict) -> dict:
    """支持编辑：original_slug 变更时删除旧文件与列表项。"""
    original = (payload.get("original_slug") or "").strip()
    # 安全：original_slug 会被拼进 unlink() 的路径，必须严格校验。
    # 这里不做 slugify 回退，避免 "../index" 被“修正”成合法值后误删其它文件。
    if original and not SLUG_RE.match(original):
        raise ValueError("original_slug 无效")
    result = save_post(payload)
    if original and original != result["slug"]:
        with _lock:
            old_html = POSTS_DIR / f"{original}.html"
            old_md = POSTS_DIR / f"{original}.md"
            if old_html.exists():
                old_html.unlink()
            if old_md.exists():
                old_md.unlink()
            remove_list_entries(original)
        # 移除旧条目后新条目仍在列表最前，无需再插
    return result


# ---------- 导入：多格式 → Markdown ----------

SUPPORTED_IMPORT_EXT = {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".zip"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
DATA_URI_RE = re.compile(
    r"data:image/(png|jpeg|jpg|gif|webp|svg\+xml);base64,([A-Za-z0-9+/=\s]+)",
    re.I,
)

# 站点允许通过 HTTP 对外输出的静态资源类型（白名单）。
# 源码 / 备份 / 配置 / 数据文件一律不在其列，避免 server.py、*.bak、
# data/*.json 之类被直接下载。
STATIC_ALLOW_EXT = {
    ".html", ".htm", ".css", ".js", ".mjs",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".txt", ".xml", ".webmanifest", ".map",
}

# 压缩包（zip/docx）解压限额，用于阻断压缩炸弹与超量解压
MAX_ZIP_ENTRIES = 800
MAX_ZIP_ENTRY_BYTES = 20 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ZIP_RATIO = 200


def _zip_guard(zf: zipfile.ZipFile, name: str, raw_size: int = 0) -> None:
    """解压前按中央目录元数据校验体积，拦截 zip 炸弹。"""
    try:
        infos = zf.infolist()
    except Exception:
        raise ValueError(f"{name}: 压缩包已损坏")
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"{name}: 压缩包内文件数量过多（上限 {MAX_ZIP_ENTRIES} 个）")
    total = 0
    for info in infos:
        if info.file_size > MAX_ZIP_ENTRY_BYTES:
            raise ValueError(f"{name}: 压缩包内单个文件过大（上限 20MB）")
        total += info.file_size
    if total > MAX_ZIP_TOTAL_BYTES:
        raise ValueError(f"{name}: 解压后体积过大（上限 50MB）")
    if raw_size:
        floor = max(raw_size * MAX_ZIP_RATIO, 8 * 1024 * 1024)
        if total > floor:
            raise ValueError(f"{name}: 压缩比异常，疑似压缩炸弹，已拒绝")


def _detect_image_ext(data: bytes) -> str | None:
    """按文件内容魔数判定真实图片类型。

    上传接口里 format / 扩展名都由客户端自报，不可信；如果不按内容判定，
    攻击者可以把脚本存成 .js 落在同源目录，从而绕过站点的 CSP。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    low = data.lower()
    if b"<svg" in low:
        # SVG 是同源可执行内容：含脚本 / 事件属性 / 嵌套文档一律拒绝
        if (
            b"<script" in low
            or b"javascript:" in low
            or b"<foreignobject" in low
            or b"<iframe" in low
            or b"<embed" in low
            or re.search(rb"\son[a-z]+\s*=", low)
        ):
            return None
        return ".svg"
    return None


def _safe_slug_dir(slug: str) -> str:
    """上传目录名：只留字母/数字/中文/下划线/连字符，杜绝路径穿越。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", str(slug or "")).strip("-._")
    if not s:
        return "misc"
    return s[:80]


def _log_internal_error(where: str, exc: Exception) -> None:
    """内部异常只写服务端日志，不回显给客户端（避免堆栈/路径泄露）。"""
    try:
        print(
            f"[ERROR] {datetime.now().isoformat(timespec='seconds')} {where}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
    except Exception:
        pass


def _safe_filename(name: str, fallback: str = "image") -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\-一-鿿]+", "_", base).strip("._") or fallback
    if len(base) > 80:
        stem, dot, ext = base.rpartition(".")
        base = (stem[:60] + dot + ext) if dot else base[:80]
    return base


def save_upload_bytes(slug: str, filename: str, data: bytes) -> str:
    """写入 assets/uploads/<slug>/<filename>，返回站内绝对路径。

    安全约束：
      1. 目录名经 _safe_slug_dir 清洗，禁止路径穿越；
      2. 落盘扩展名一律由**文件内容魔数**决定，忽略客户端自报的后缀，
         避免把 .js / .html 等同源可执行内容写进站点（可绕过 CSP）；
      3. 非图片内容（含脚本的 svg 等）直接拒绝。
    """
    if not data:
        raise ValueError("空图片数据")
    ext = _detect_image_ext(data)
    if ext is None:
        raise ValueError("不支持的图片格式（仅允许 png/jpg/gif/webp/bmp 及安全的 svg）")
    safe_slug = _safe_slug_dir(slug)
    stem = Path(_safe_filename(filename, fallback="image")).stem.strip("._-") or "image"
    if len(stem) > 60:
        stem = stem[:60]
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    folder = UPLOADS_DIR / safe_slug
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{stem}{ext}"
    # 避免覆盖不同内容
    target = folder / fname
    if target.exists() and target.read_bytes() != data:
        n = 1
        while True:
            alt = f"{stem}_{n}{ext}"
            target = folder / alt
            if not target.exists() or target.read_bytes() == data:
                fname = alt
                break
            n += 1
    target.write_bytes(data)
    return f"/assets/uploads/{safe_slug}/{fname}"


def extract_data_uri_images(text: str, slug: str) -> str:
    """把文本里的 data:image 内联图落盘并改写为站内路径。"""
    if "data:image/" not in text:
        return text
    return _replace_data_uris_in_text(text, slug)


def _replace_data_uris_in_text(text: str, slug: str) -> str:
    def repl(m: re.Match) -> str:
        fmt = m.group(1).lower().replace("jpeg", "jpg")
        b64 = re.sub(r"\s+", "", m.group(2))
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return m.group(0)
        ext = "svg" if "svg" in fmt else fmt
        try:
            return save_upload_bytes(slug, f"inline_{abs(hash(b64[:64])) % 10**10}.{ext}", raw)
        except Exception:
            # 不支持的格式（例如带脚本的 svg）保持原样，不阻断正文保存
            return m.group(0)

    return DATA_URI_RE.sub(repl, text)


def _docx_relationships(zf: zipfile.ZipFile) -> dict[str, str]:
    """rId -> media path"""
    rels_path = "word/_rels/document.xml.rels"
    mapping: dict[str, str] = {}
    if rels_path not in zf.namelist():
        return mapping
    try:
        root = ET.fromstring(zf.read(rels_path))
    except Exception:
        return mapping
    for rel in root:
        rid = rel.get("Id") or ""
        target = rel.get("Target") or ""
        typ = rel.get("Type") or ""
        if "image" not in typ and "/media/" not in target:
            continue
        if not rid:
            continue
        # Target 相对 word/
        media = target
        if media.startswith("/"):
            media = media.lstrip("/")
        elif not media.startswith("word/"):
            media = "word/" + media
        media = media.replace("\\", "/")
        mapping[rid] = media
    return mapping


class _HTMLToMarkdown(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._list_stack: list[str] = []
        self._in_pre = False
        self._in_code = False
        self._in_blockquote = 0
        self._href = ""
        self._in_a = False
        self._title = ""
        self._in_title = False
        self._in_h1 = False
        self._h1 = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self.parts.append("\n\n# ")
        elif tag == "h2":
            self.parts.append("\n\n## ")
        elif tag in ("h3", "h4", "h5", "h6"):
            self.parts.append("\n\n### ")
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "hr":
            self.parts.append("\n\n---\n\n")
        elif tag == "strong" or tag == "b":
            self.parts.append("**")
        elif tag == "em" or tag == "i":
            self.parts.append("*")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
            self._in_code = True
        elif tag == "pre":
            self.parts.append("\n\n```\n")
            self._in_pre = True
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self.parts.append("\n")
        elif tag == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            bullet = "- " if (not self._list_stack or self._list_stack[-1] == "ul") else "1. "
            self.parts.append(f"\n{indent}{bullet}")
        elif tag == "blockquote":
            self._in_blockquote += 1
            self.parts.append("\n\n")
        elif tag == "a":
            self._in_a = True
            self._href = attrs.get("href") or ""
            self.parts.append("[")
        elif tag == "img":
            alt = attrs.get("alt") or "image"
            src = attrs.get("src") or ""
            if src:
                self.parts.append(f"![{alt}]({src})")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
            self.parts.append("\n")
        elif tag in ("h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")
        elif tag == "strong" or tag == "b":
            self.parts.append("**")
        elif tag == "em" or tag == "i":
            self.parts.append("*")
        elif tag == "code" and self._in_code and not self._in_pre:
            self.parts.append("`")
            self._in_code = False
        elif tag == "pre":
            self.parts.append("\n```\n\n")
            self._in_pre = False
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self.parts.append("\n")
        elif tag == "blockquote":
            self._in_blockquote = max(0, self._in_blockquote - 1)
            self.parts.append("\n")
        elif tag == "a":
            href = self._href
            self._in_a = False
            self._href = ""
            if href:
                self.parts.append(f"]({href})")
            else:
                self.parts.append("]")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self._title += data
            return
        if self._in_h1:
            self._h1 += data
        if self._in_pre or self._in_code:
            self.parts.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip() and not self.parts:
            return
        self.parts.append(text)

    def markdown(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    def title(self) -> str:
        return re.sub(r"\s+", " ", (self._title or self._h1)).strip()


def html_to_markdown(content: str) -> tuple[str, str, list[tuple[str, bytes]]]:
    """转换 HTML；抽取内联 data:image 为图片文件。"""
    embedded: list[tuple[str, bytes]] = []

    def pull_data_imgs(html_text: str) -> str:
        def repl(m: re.Match) -> str:
            quote = m.group(1)
            fmt = m.group(2).lower().replace("jpeg", "jpg")
            b64 = re.sub(r"\s+", "", m.group(3))
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return m.group(0)
            ext = "svg" if "svg" in fmt else fmt
            fname = f"img_{len(embedded) + 1}.{ext}"
            embedded.append((fname, raw))
            return f"src={quote}@htmlimg:{fname}{quote}"

        return re.sub(
            r"src=(['\"])data:image/([^;'\"]+);base64,([^'\"]+)\1",
            repl,
            html_text,
            flags=re.I,
        )

    cleaned = pull_data_imgs(content)
    parser = _HTMLToMarkdown()
    try:
        parser.feed(cleaned)
        parser.close()
    except Exception:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", cleaned)
        text = re.sub(r"(?s)<[^>]+>", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return "", text, embedded
    return parser.title(), parser.markdown(), embedded


def _docx_paragraph_text(p: ET.Element) -> str:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    chunks: list[str] = []
    for node in p.iter():
        tag = node.tag.split("}")[-1]
        if tag == "t" and node.text:
            chunks.append(node.text)
        elif tag == "tab":
            chunks.append("\t")
        elif tag == "br":
            chunks.append("\n")
    text = "".join(chunks).strip()
    if not text:
        return ""
    style = ""
    pPr = p.find("w:pPr", ns)
    if pPr is not None:
        pStyle = pPr.find("w:pStyle", ns)
        if pStyle is not None:
            style = (pStyle.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or "").lower()
    if "heading1" in style or style == "1":
        return f"# {text}"
    if "heading2" in style or style == "2":
        return f"## {text}"
    if "heading3" in style or style.startswith("heading"):
        return f"### {text}"
    if style in ("listparagraph", "list"):
        return f"- {text}"
    return text


def _docx_relationships(zf: zipfile.ZipFile) -> dict[str, str]:
    rels_path = "word/_rels/document.xml.rels"
    mapping: dict[str, str] = {}
    if rels_path not in zf.namelist():
        return mapping
    try:
        root = ET.fromstring(zf.read(rels_path))
    except Exception:
        return mapping
    for rel in root:
        rid = rel.get("Id") or ""
        target = rel.get("Target") or ""
        typ = rel.get("Type") or ""
        if "image" not in typ and "/media/" not in target:
            continue
        if not rid:
            continue
        media = target.replace("\\", "/")
        if media.startswith("/"):
            media = media.lstrip("/")
        elif not media.startswith("word/"):
            media = "word/" + media
        mapping[rid] = media
    return mapping


def docx_to_markdown(raw: bytes) -> tuple[str, str, list[tuple[str, bytes]]]:
    """从 docx 抽取文本与内嵌图片（无第三方依赖）。"""
    import io

    title = ""
    bio = io.BytesIO(raw)
    with zipfile.ZipFile(bio) as zf:
        _zip_guard(zf, "docx", len(raw))
        names = set(zf.namelist())
        if "docProps/core.xml" in names:
            try:
                core = ET.fromstring(zf.read("docProps/core.xml"))
                for el in core.iter():
                    if el.tag.endswith("title") and el.text and el.text.strip():
                        title = el.text.strip()
                        break
            except Exception:
                pass
        if "word/document.xml" not in names:
            raise ValueError("不是有效的 docx 文件")

        rid_map = _docx_relationships(zf)
        images: list[tuple[str, bytes]] = []
        seen_rids: set[str] = set()
        media_files = {n for n in names if n.startswith("word/media/") and not n.endswith("/")}

        doc = ET.fromstring(zf.read("word/document.xml"))
        ns = {
            "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        }
        r_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        lines: list[str] = []
        for p in doc.findall(".//w:p", ns):
            line = _docx_paragraph_text(p)
            for blip in p.findall(".//a:blip", ns):
                rid = blip.get(r_attr) or ""
                if not rid or rid in seen_rids or rid not in rid_map:
                    continue
                media_path = rid_map[rid]
                if media_path not in media_files and media_path not in names:
                    continue
                try:
                    img_bytes = zf.read(media_path)
                except Exception:
                    continue
                fname = Path(media_path).name
                images.append((fname, img_bytes))
                seen_rids.add(rid)
                line = ((line + "\n\n") if line else "") + f"![{fname}](@docx:{rid}:{fname})"
            if line:
                lines.append(line)

        if not images:
            for mf in sorted(media_files):
                try:
                    images.append((Path(mf).name, zf.read(mf)))
                except Exception:
                    continue

    md = "\n\n".join(lines).strip()
    return title, md, images


def decode_import_content(file: dict) -> tuple[str, str, str, dict, list[tuple[str, bytes]]]:
    """返回 (title, markdown, filename, meta, images)。"""
    name = (file.get("name") or "untitled").strip()
    fmt = (file.get("format") or Path(name).suffix.lstrip(".")).lower()
    content = file.get("content") or ""
    encoding = file.get("encoding") or "utf-8"

    raw_bytes: bytes | None = None
    if file.get("base64"):
        try:
            raw_bytes = base64.b64decode(content)
        except Exception:
            raise ValueError(f"{name}: base64 解码失败")

    ext = Path(name).suffix.lower()
    if fmt in ("docx",) or ext == ".docx":
        if raw_bytes is None:
            try:
                raw_bytes = base64.b64decode(content)
            except Exception:
                raise ValueError(f"{name}: docx 需要 base64 内容")
        title, md, images = docx_to_markdown(raw_bytes)
        return title or Path(name).stem, md, name, {}, images

    if fmt in ("zip",) or ext == ".zip":
        if raw_bytes is None:
            try:
                raw_bytes = base64.b64decode(content)
            except Exception:
                raise ValueError(f"{name}: zip 需要 base64 内容")
        title, md, images, meta = _import_from_zip(raw_bytes, name)
        return title, md, name, meta, images

    if isinstance(content, str) and raw_bytes is None:
        text = content
    else:
        raw_bytes = raw_bytes or content.encode("utf-8", errors="replace")
        try:
            text = raw_bytes.decode(encoding)
        except Exception:
            text = raw_bytes.decode("utf-8", errors="replace")

    if fmt in ("html", "htm") or ext in (".html", ".htm"):
        title, md, images = html_to_markdown(text)
        return title or Path(name).stem, md, name, {}, images

    # md / txt / markdown
    meta, body = parse_front_matter(text)
    md_title = (meta.get("title") or "").strip()
    if not md_title:
        for line in body.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("# "):
                md_title = s[2:].strip()
            elif s.startswith("## "):
                md_title = s[3:].strip()
            else:
                md_title = s[:40]
            break
    result_title = md_title or Path(name).stem
    return result_title, body, name, meta, []


def _import_from_zip(raw: bytes, name: str) -> tuple[str, str, list[tuple[str, bytes]], dict]:
    """zip 内含 md/html 与图片目录时，抽出正文并保留相对路径图片。"""
    import io

    if not raw:
        raise ValueError(f"{name}: zip 内容为空")
    images: list[tuple[str, bytes]] = []
    doc_text = ""
    doc_name = ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        raise ValueError(f"{name}: 不是有效的 zip")

    with zf:
        _zip_guard(zf, name, len(raw))
        names = zf.namelist()
        doc_candidates = [
            n for n in names
            if n.lower().endswith((".md", ".markdown", ".html", ".htm"))
            and not Path(n).name.startswith(".")
            and "/__MACOSX/" not in n
        ]
        # 优先根目录 md，再 html
        doc_candidates.sort(key=lambda n: (n.count("/"), 0 if n.lower().endswith((".md", ".markdown")) else 1, n))
        if not doc_candidates:
            raise ValueError(f"{name}: zip 内未找到 md/html")
        doc_name = doc_candidates[0]
        doc_bytes = zf.read(doc_name)
        try:
            doc_text = doc_bytes.decode("utf-8")
        except Exception:
            doc_text = doc_bytes.decode("utf-8", errors="replace")

        for n in names:
            if n.endswith("/") or "/__MACOSX/" in n:
                continue
            extn = Path(n).suffix.lower()
            if extn not in IMG_EXT:
                continue
            try:
                images.append((Path(n).name, zf.read(n)))
            except Exception:
                continue

    # 解析正文
    if doc_name.lower().endswith((".html", ".htm")):
        title, md, emb = html_to_markdown(doc_text)
        images = images + emb
        meta: dict = {}
    else:
        meta, body = parse_front_matter(doc_text)
        title = (meta.get("title") or "").strip()
        md = body
        if not title:
            for line in md.splitlines():
                s = line.strip()
                if s.startswith("# "):
                    title = s[2:].strip()
                    break
                if s:
                    title = s[:40]
                    break
        title = title or Path(doc_name).stem

    # 把 md 里相对路径图片名映射到已打包的同名文件
    return title, md, images, meta


def materialize_import_images(slug: str, md: str, images: list[tuple[str, bytes]]) -> str:
    """把图片写入 uploads，并把占位符/相对路径改写为 /assets/uploads/..."""
    if not images:
        return extract_data_uri_images(md, slug)

    saved_map: dict[str, str] = {}  # basename -> url
    for fname, data in images:
        try:
            url = save_upload_bytes(slug, fname, data)
            saved_map[fname] = url
            # 也按不含扩展名的 stem 记录，便于匹配 Word 导出名
            stem = Path(fname).stem
            if stem and stem not in saved_map:
                saved_map[stem] = url
        except Exception:
            continue

    # @docx:rid:filename
    md = re.sub(
        r"!\[([^\]]*)\]\(@docx:([^:]+):([^)]+)\)",
        lambda m: f"![{m.group(1)}]({saved_map.get(m.group(3), saved_map.get(Path(m.group(3)).stem, m.group(3)))})",
        md,
    )

    # @htmlimg:filename
    for fname, url in list(saved_map.items()):
        md = md.replace(f"@htmlimg:{fname}", url)

    # 任意相对/绝对本地路径里的已知文件名 → 站内 URL
    # 例如 images/image-20250911092344970.png 或 ./img/foo.jpg
    def rewrite_rel(m: re.Match) -> str:
        alt = m.group(1)
        src = m.group(2).strip()
        if src.startswith(("http://", "https://", "/assets/", "data:", "@")):
            return m.group(0)
        base = Path(src.replace("\\", "/")).name
        stem = Path(base).stem
        url = saved_map.get(base) or saved_map.get(stem)
        if url:
            return f"![{alt}]({url})"
        return m.group(0)

    md = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", rewrite_rel, md)

    # HTML 里残留的 src 相对路径
    for fname, url in saved_map.items():
        md = re.sub(
            rf'src=["\'](?:[\w./\\-]+/)?{re.escape(fname)}["\']',
            f'src="{url}"',
            md,
            flags=re.I,
        )

    md = extract_data_uri_images(md, slug)
    return md


def _is_image_file(file: dict) -> bool:
    name = (file.get("name") or "").lower()
    fmt = (file.get("format") or "").lower()
    ext = Path(name).suffix.lower()
    return fmt == "image" or ext in IMG_EXT


def _decode_image_file(file: dict) -> tuple[str, bytes] | None:
    name = Path((file.get("name") or "image.bin")).name
    content = file.get("content") or ""
    raw: bytes | None = None
    if file.get("base64") or fmt_is_b64(file):
        try:
            raw = base64.b64decode(content)
        except Exception:
            return None
    elif isinstance(content, str):
        # 不该走这里；图片必须 base64
        return None
    if not raw:
        return None
    # 只信内容魔数：name / format 都是客户端自报，不可作为类型依据
    if _detect_image_ext(raw) is None:
        return None
    return name, raw


def fmt_is_b64(file: dict) -> bool:
    return bool(file.get("base64"))


def import_files(files: list[dict], default_tag: str = "随笔", overwrite: bool = False) -> dict:
    if not files:
        raise ValueError("没有可导入的文件")
    if len(files) > 50:
        raise ValueError("一次最多导入 50 个文件")
    default_tag = normalize_tag(default_tag)

    # 拆出同批图片，供 md/html 相对路径匹配
    companion_images: list[tuple[str, bytes]] = []
    docs: list[dict] = []
    for f in files:
        if _is_image_file(f):
            pair = _decode_image_file(f)
            if pair:
                companion_images.append(pair)
        else:
            docs.append(f)

    if not docs and companion_images:
        raise ValueError("只选了图片，请同时选择 md/html/docx 正文")
    if not docs:
        docs = files  # 兜底

    results = []
    for f in docs:
        title, md, name, meta, images = decode_import_content(f)
        title = (title or Path(name).stem).strip()[:80]
        date_iso = (
            (f.get("date") or "").strip()
            or (meta.get("date") or "").strip()
            or date.today().isoformat()
        )
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_iso):
            date_iso = date.today().isoformat()
        tag = normalize_tag(f.get("tag") or meta.get("tag") or default_tag)
        slug = slugify(title)
        exists = (POSTS_DIR / f"{slug}.html").exists() or (POSTS_DIR / f"{slug}.md").exists()
        if exists and not overwrite:
            results.append({"ok": False, "name": name, "error": f"slug 已存在：{slug}"})
            continue
        try:
            all_images = list(images) + list(companion_images)
            md = materialize_import_images(slug, md, all_images)
            # 统计仍缺失的相对图
            missing = re.findall(
                r"!\[([^\]]*)\]\(((?!https?://|/assets/|data:)[^)\s]+)\)",
                md,
            )
            excerpt_src = (meta.get("excerpt") or " ".join(md.split())[:60]).strip()
            saved = save_post(
                {
                    "title": title,
                    "tag": tag,
                    "date": date_iso,
                    "excerpt": excerpt_src,
                    "content": md,
                    "slug": slug,
                    "overwrite": True,
                }
            )
            results.append(
                {
                    "ok": True,
                    "name": name,
                    "images": len(all_images),
                    "missing_images": [m[1] for m in missing][:10],
                    **saved,
                }
            )
        except Exception as e:
            results.append({"ok": False, "name": name, "error": str(e)})

    ok_n = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_n > 0, "imported": ok_n, "total": len(results), "results": results}


def parse_import_files(files: list[dict], default_tag: str = "随笔") -> dict:
    """只解析不发布：返回可填入编辑器的文章草稿。多篇时取第一篇正文。"""
    if not files:
        raise ValueError("没有可导入的文件")

    companion_images: list[tuple[str, bytes]] = []
    docs: list[dict] = []
    for f in files:
        if _is_image_file(f):
            pair = _decode_image_file(f)
            if pair:
                companion_images.append(pair)
        else:
            docs.append(f)
    if not docs:
        raise ValueError("未找到正文文件（md/html/docx/txt/zip）")

    f = docs[0]
    title, md, name, meta, images = decode_import_content(f)
    title = (title or Path(name).stem).strip()[:80]
    tag = normalize_tag(f.get("tag") or meta.get("tag") or default_tag)
    date_iso = (
        (f.get("date") or "").strip()
        or (meta.get("date") or "").strip()
        or date.today().isoformat()
    )
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_iso):
        date_iso = date.today().isoformat()

    slug = slugify(title)
    all_images = list(images) + list(companion_images)
    md = materialize_import_images(slug, md, all_images)
    excerpt = (meta.get("excerpt") or "").strip() or generate_excerpt(md)
    missing = re.findall(
        r"!\[([^\]]*)\]\(((?!https?://|/assets/|data:)[^)\s]+)\)",
        md,
    )

    return {
        "ok": True,
        "source": name,
        "title": title,
        "tag": tag,
        "date": date_iso,
        "excerpt": excerpt,
        "content": md,
        "slug": slug,
        "images": len(all_images),
        "missing_images": [m[1] for m in missing][:10],
        "other_docs": [d.get("name") for d in docs[1:8]],
    }


# ---------- HTTP ----------

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    ),
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "JiejieBlog/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _apply_headers(self, extra: dict | None = None, cache: str = "no-cache"):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Cache-Control", cache)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, cache: str = "no-cache"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._apply_headers(extra, cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj, extra: dict | None = None):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8", extra)

    def _html_file(self, path: Path, code: int = 200, extra: dict | None = None):
        data = path.read_bytes()
        self._send(code, data, "text/html; charset=utf-8", extra)

    def _redirect(self, location: str, code: int = 302):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._apply_headers()
        self.end_headers()

    def _client_ip(self) -> str:
        # 反向代理场景下 client_address 恒为代理地址，会导致限速桶变成全局限速；
        # 取 X-Forwarded-For 最后一段（最近一跳，由可信反代写入）作为真实客户端 IP。
        xff = self.headers.get("X-Forwarded-For") or ""
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1][:64]
        return self.client_address[0] if self.client_address else "?"

    def _cookies(self) -> SimpleCookie:
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        return c

    def _session(self, cfg: dict) -> dict | None:
        cookies = self._cookies()
        morsel = cookies.get("jiejie_session")
        return load_session(morsel.value if morsel else None, cfg)

    def _read_json(self, max_bytes: int = 15_000_000) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > max_bytes:
            if length > max_bytes:
                raise ValueError(f"请求过大（上限 {max_bytes // (1024 * 1024)}MB）")
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def _safe_path(self, rel: str) -> Path | None:
        rel = unquote(rel).lstrip("/")
        if not rel:
            return None
        parts = rel.replace("\\", "/").split("/")
        if any(p == ".." for p in parts):
            return None
        if parts and parts[0] in ("data", "tools", "nginx"):
            return None
        if any(p.startswith(".") for p in parts):
            return None
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            return None
        return target

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        cfg = ensure_config()
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/admin", "/admin/"):
            return self._redirect("/admin/write" if self._session(cfg) else "/admin/login")

        if path == "/admin/login":
            if self._session(cfg):
                return self._redirect("/admin/write")
            return self._html_file(ADMIN_DIR / "login.html")

        if path == "/admin/write":
            if not self._session(cfg):
                return self._redirect("/admin/login")
            return self._html_file(ADMIN_DIR / "write.html")

        if path == "/api/auth/me":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "authed": False})
            return self._json(
                200,
                {
                    "ok": True,
                    "authed": True,
                    "user": sess["user"],
                    "csrf": sess["csrf"],
                    "tags": collect_tags(),
                    "today": date.today().isoformat(),
                },
            )

        if path == "/api/posts":
            if not self._session(cfg):
                return self._json(401, {"ok": False, "error": "请先登录"})
            return self._json(200, {"ok": True, "posts": list_posts()})

        m_get = re.match(r"^/api/posts/([^/]+)$", path)
        if m_get:
            if not self._session(cfg):
                return self._json(401, {"ok": False, "error": "请先登录"})
            try:
                return self._json(200, {"ok": True, "post": load_post(unquote(m_get.group(1)))})
            except FileNotFoundError:
                return self._json(404, {"ok": False, "error": "文章不存在"})
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})

        # 公开静态
        if path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "not found"})

        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = self._safe_path(rel)
        if target is None:
            return self._json(403, {"ok": False, "error": "forbidden"})
        if target.is_file():
            # 只放行站点真正需要的静态类型；源码 / 备份 / 配置 / 数据文件
            # 一律不通过 HTTP 输出（server.py.bak 之类曾可被公网直接下载）。
            if target.suffix.lower() not in STATIC_ALLOW_EXT:
                return self._send(404, b"Not Found", "text/plain; charset=utf-8")
            ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
            # 管理页与脚本样式避免缓存，保证改完立刻生效
            if path.startswith("/admin/") or target.suffix.lower() in {".js", ".css"}:
                cache = "no-store"
            else:
                cache = "public, max-age=300" if target.suffix.lower() in {".svg"} else "no-cache"
            return self._send(200, target.read_bytes(), ctype, cache=cache)
        if target.is_dir() and (target / "index.html").is_file():
            return self._send(200, (target / "index.html").read_bytes(), "text/html; charset=utf-8")
        return self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        cfg = ensure_config()
        path = urlparse(self.path).path
        ip = self._client_ip()

        try:
            payload = self._read_json()
        except Exception:
            return self._json(400, {"ok": False, "error": "JSON 无效"})

        if path == "/api/auth/login":
            if not check_rate_limit(ip):
                return self._json(429, {"ok": False, "error": "尝试过于频繁，请 5 分钟后再试"})
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
            # 恒定工作流：无论用户名是否存在都完整执行一次 PBKDF2，
            # 消除“用户名不存在即短路”造成的可枚举时间侧信道。
            password_ok = verify_password(password, cfg)
            username_ok = hmac.compare_digest(username, str(cfg.get("username") or ""))
            if not (username_ok and password_ok):
                time.sleep(0.4)
                return self._json(401, {"ok": False, "error": "用户名或密码错误"})
            token = create_session(cfg)
            cookie = f"jiejie_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_HOURS * 3600}"
            if os.environ.get("JIEJIE_SECURE_COOKIE", "").lower() in ("1", "true", "yes"):
                cookie += "; Secure"
            with _lock:
                _login_attempts.pop(ip, None)
            return self._json(200, {"ok": True, "redirect": "/admin/write"}, {"Set-Cookie": cookie})

        if path == "/api/auth/logout":
            cookies = self._cookies()
            morsel = cookies.get("jiejie_session")
            destroy_session(morsel.value if morsel else None, cfg)
            return self._json(
                200,
                {"ok": True},
                {"Set-Cookie": "jiejie_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"},
            )

        if path == "/api/posts/save":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "error": "请先登录"})
            csrf = str(payload.get("csrf") or "")
            if not hmac.compare_digest(csrf, sess["csrf"]):
                return self._json(403, {"ok": False, "error": "CSRF 校验失败，请刷新页面"})
            try:
                result = save_post_with_rename(payload)
                return self._json(200, result)
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                _log_internal_error("POST " + path, e)
                return self._json(500, {"ok": False, "error": "服务器内部错误"})

        if path == "/api/posts/pin":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "error": "请先登录"})
            csrf = str(payload.get("csrf") or "")
            if not hmac.compare_digest(csrf, sess["csrf"]):
                return self._json(403, {"ok": False, "error": "CSRF 校验失败，请刷新页面"})
            slug = str(payload.get("slug") or "").strip()
            if not slug:
                return self._json(400, {"ok": False, "error": "缺少 slug"})
            pinned = bool(payload.get("pinned"))
            try:
                with _lock:
                    if not (POSTS_DIR / f"{slug}.html").exists() and not (POSTS_DIR / f"{slug}.md").exists():
                        return self._json(404, {"ok": False, "error": "文章不存在"})
                    set_pinned(slug, pinned)
                    rebuild_site_lists()
                return self._json(200, {"ok": True, "slug": slug, "pinned": pinned})
            except Exception as e:
                _log_internal_error("POST " + path, e)
                return self._json(500, {"ok": False, "error": "服务器内部错误"})

        if path == "/api/posts/delete":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "error": "请先登录"})
            csrf = str(payload.get("csrf") or "")
            if not hmac.compare_digest(csrf, sess["csrf"]):
                return self._json(403, {"ok": False, "error": "CSRF 校验失败，请刷新页面"})
            try:
                result = delete_post(str(payload.get("slug") or ""))
                return self._json(200, result)
            except FileNotFoundError:
                return self._json(404, {"ok": False, "error": "文章不存在"})
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                _log_internal_error("POST " + path, e)
                return self._json(500, {"ok": False, "error": "服务器内部错误"})

        if path == "/api/posts/parse":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "error": "请先登录"})
            csrf = str(payload.get("csrf") or "")
            if not hmac.compare_digest(csrf, sess["csrf"]):
                return self._json(403, {"ok": False, "error": "CSRF 校验失败，请刷新页面"})
            try:
                files = payload.get("files") or []
                if not isinstance(files, list):
                    raise ValueError("files 必须是数组")
                result = parse_import_files(
                    files,
                    default_tag=str(payload.get("tag") or "随笔"),
                )
                return self._json(200, result)
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                _log_internal_error("POST " + path, e)
                return self._json(500, {"ok": False, "error": "服务器内部错误"})

        if path == "/api/posts/import":
            sess = self._session(cfg)
            if not sess:
                return self._json(401, {"ok": False, "error": "请先登录"})
            csrf = str(payload.get("csrf") or "")
            if not hmac.compare_digest(csrf, sess["csrf"]):
                return self._json(403, {"ok": False, "error": "CSRF 校验失败，请刷新页面"})
            try:
                files = payload.get("files") or []
                if not isinstance(files, list):
                    raise ValueError("files 必须是数组")
                result = import_files(
                    files,
                    default_tag=str(payload.get("tag") or "随笔"),
                    overwrite=bool(payload.get("overwrite")),
                )
                return self._json(200, result)
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                _log_internal_error("POST " + path, e)
                return self._json(500, {"ok": False, "error": "服务器内部错误"})

        return self._json(404, {"ok": False, "error": "not found"})


def main():
    ensure_config()
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _lock:
            rebuild_site_lists()
    except Exception:
        pass
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"杰杰的博客已启动: http://{HOST}:{PORT}/")
    print(f"管理写作: http://{HOST}:{PORT}/admin/login")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
