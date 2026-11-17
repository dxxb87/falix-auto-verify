#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Falix qawqaw 自动唤醒（自动验证版）

思路参照 eooce/Auto-Renew-Bothosting（GPL-3.0）：
  - SeleniumBase UC 模式 + Xvfb 虚拟显示（不用 headless）
  - 等 Turnstile 出现后，用系统级真实鼠标点击（xdotool / CDP gui_click）
    代替 Selenium 的普通点击，并轮询 cf-turnstile-response token 判断是否通过
  - README 里那句很关键：“有 cf 盾，太垃圾的机房节点可能过不了，
    建议用稍微干净点的节点”——所以这里同样支持干净代理（PROXY_SERVER），
    没有干净 IP 时，GitHub Actions 的机房 IP 很可能和 Muse 云浏览器一样失败。

流程：
  1. 先用 Falix API 查服务器状态
     - offline/storage：先发一次 API start，403 verification_required
       响应体里的 action_url 就是当次的唤醒页（无需登录），打开它
       过 Turnstile，拿到 token 后点 "Start the server"，
       再用 API 查一次状态，确认是否 online / starting
     - running：再读控制台日志 GET /servers/{id}/console/log，
       找 "Are you still there? Verify within 30 min: .../verify?t=..."
       的 AFK 验证链接，找到就打开它过 Turnstile 点 Verify，
       没找到直接退出（2026-11-15 新增，AFK 窗口只有 30 分钟，
       所以 workflow 要每 10 分钟跑一次才赶得上）
  2. VERIFY_URL（环境变量/config.py/直写）可直接指定一条 AFK 链接，
     手动跑时把游戏里那条链接填进去就行，优先级高于日志。

  多账号（2026-11-15 新增）：FALIX_BATCH 一行一个服，
     格式 api_key,server_id，填了就按批量跑（每个账号独立开
     浏览器、独立查状态），不填就走上面单账号的老配置。代理
     所有账号共用同一份（NODE_LINK / PROXY_SERVER），账号
     之间间隔 5 秒。密钥只放 Secrets，不要写进代码；批量里
     字段前后多余的单双引号会自动去掉（贴 export ...='...' 时
     容易把引号一起贴进来）。同一个账号有多个服时，key 重复
     写多行就行。
  两个链接都不要写进配置，每次跑都是 API 当场给的：
     AFK 验证链接（verify?t=...）从各服控制台日志现读，
     token 每次都不一样；唤醒页链接从 API 发 start 后返回的
     403 verification_required 响应体里的 action_url 现取，
     拿不到时才用 ACTION_URL 默认值兜底。

配置（公库 / 私库共用，优先级：非空环境变量 > config.py > 下面直写占位 > 默认值）：
  公库：直写占位留空，全部用 GitHub Secrets / 环境变量，config.py 不要提交。
  私库：可以直接填 config.py（推荐，config.py 已进 .gitignore），
        也可以填下面这组直写占位，但填了之后不要再把仓库改成公开。
  FALIX_API_KEY   Falix API Key（servers:read + servers:power）
  SERVER_ID       默认 3564044（qawqaw）
  ACTION_URL      默认 https://falixnodes.net/s/2dxiy1giuhvyk
  NODE_LINK       可选，代理链接，如 hysteria2:// / vless:// / vmess:// /
                  tuic:// / anytls:// / ss://，由 setup_proxy.py 转本地代理
  PROXY_SERVER    可选，例如 http://127.0.0.1:1080 或住宅代理地址；
                  填了 NODE_LINK 时一般不用再填这个
  HEADLESS        默认 false（配合 xvfb-run 使用）
  TG_BOT_TOKEN / TG_CHAT_ID  可选，Telegram 通知；服务器离线且唤醒失败时
                             （Turnstile 未通过 / 按钮点不动 / 120 秒未上线 /
                              未检测到 Turnstile / 处理异常）会推送一条，
                             AFK 验证失败也推。没配就不推，不影响运行。
注意：环境变量为空字符串时不算已配置，不会覆盖 config.py / 直写值，
      这样 workflow 里 Secrets 留空也不会把硬编码顶掉。
"""

import os
import re
import sys
import time
import random
import subprocess

import requests

try:
    from seleniumbase import SB
except ImportError:  # pragma: no cover
    SB = None

API_BASE = "https://client.falixnodes.net/api/v2"

# ---- 公私共用配置读取 ----
# 私库直写区：只有确认仓库永远是私有的才填这里，填了就不要改公开。
# 这里 shipped 的版本全部留空，只做占位，不含任何真实密钥。
HARDCODED = {
    "FALIX_API_KEY": "",
    "FALIX_BATCH": "",
    "NODE_LINK": "",
    "PROXY_SERVER": "",
    "SERVER_ID": "",
    "ACTION_URL": "",
    "VERIFY_URL": "",
    "TG_BOT_TOKEN": "",
    "TG_CHAT_ID": "",
}

try:
    import config as _local_config  # 本地 config.py（已 gitignore，优先于直写）
except ImportError:
    _local_config = None


def get_setting(name, default=""):
    """按 环境变量 > config.py > 直写占位 > 默认值 取配置，空字符串不算已配置。"""
    v = os.environ.get(name, "")
    if v and str(v).strip():
        return str(v).strip()
    if _local_config is not None:
        v = getattr(_local_config, name, "")
        if v and str(v).strip():
            return str(v).strip()
    v = HARDCODED.get(name, "")
    if v and str(v).strip():
        return str(v).strip()
    return default


SERVER_ID = get_setting("SERVER_ID", "3564044")
ACTION_URL = get_setting(
    "ACTION_URL", "https://falixnodes.net/s/2dxiy1giuhvyk"
)
FALIX_API_KEY = get_setting("FALIX_API_KEY", "")
NODE_LINK = get_setting("NODE_LINK", "")
PROXY_SERVER = get_setting("PROXY_SERVER", "")
HEADLESS = get_setting("HEADLESS", "false").lower() == "true"
TG_BOT_TOKEN = get_setting("TG_BOT_TOKEN", "")
TG_CHAT_ID = get_setting("TG_CHAT_ID", "")


def _strip_quotes(s):
    """去掉字段首尾多余的单双引号：贴 export FOO='...' 时容易把引号带进来，
    kataBump 批处理就因为这个登不上过（2026-11-15）。只去首尾一对引号，
    字段中间的引号不动。"""
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s.strip()


def parse_accounts():
    """返回账号列表，每项 {api_key, server_id, verify_url, label}。
    FALIX_BATCH 填了就批量解析（每行 api_key,server_id），
    不填就退回单账号老配置。日志里只出现 label（序号+server_id），绝不打印 key。
    注意：AFK 链接和唤醒页链接都不在这里配——AFK 链接每次从控制台日志现读，
    唤醒页链接每次从 API 的 403 响应里现取，写死在配置里会过期。
    verify_url 只有单账号模式下从 VERIFY_URL 取，那是手动跑时临时贴一条
    链接的一次性用法，不是常驻配置。"""
    batch = get_setting("FALIX_BATCH", "")
    accounts = []
    if batch:
        for lineno, raw in enumerate(batch.splitlines(), 1):
            line = (raw or "").strip()
            if not line or line.startswith("#"):
                continue
            parts = [_strip_quotes(p) for p in line.split(",")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                log(f"FALIX_BATCH 第 {lineno} 行格式不对，已跳过"
                    "（应为 api_key,server_id，一行一个服）")
                continue
            api_key, server_id = parts[0], parts[1]
            accounts.append({
                "api_key": api_key,
                "server_id": server_id,
                "verify_url": "",
                "label": f"账号{len(accounts) + 1}[{server_id}]",
            })
        if not accounts:
            log("FALIX_BATCH 里没有解析出有效账号")
        return accounts
    # 单账号兼容：老配置原样工作
    if not FALIX_API_KEY:
        return []
    return [{
        "api_key": FALIX_API_KEY,
        "server_id": SERVER_ID,
        "verify_url": get_setting("VERIFY_URL", ""),
        "label": f"账号1[{SERVER_ID}]",
    }]


def log(msg):
    print(msg, flush=True)


def debug_shot(sb, name):
    """出问题时截图存到 debug/，Actions 里用 upload-artifact 传出来看。
    2026-11-15 加：光看日志猜不出 Turnstile 到底长什么样，
    “挑战加载失败”到底是真拦了还是没加载完，截图一眼就能定论。"""
    try:
        os.makedirs("debug", exist_ok=True)
        ts = time.strftime("%H%M%S")
        fname = f"{name}_{ts}.png"
        try:
            sb.save_screenshot(fname, folder="debug")
        except TypeError:
            sb.save_screenshot(os.path.join("debug", fname))
        path = os.path.join("debug", fname)
        log(f"已截图: {path}")
        return path
    except Exception as e:
        log(f"截图失败: {e}")
        return ""


def notify(text):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram 通知失败: {e}")


def api_status(api_key, server_id):
    """返回 Falix 服务器状态字符串，失败返回 None。"""
    if not api_key:
        log("未配置 FALIX_API_KEY，跳过 API 状态检查")
        return None
    try:
        r = requests.get(
            f"{API_BASE}/servers/{server_id}/status",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"API 查状态失败: HTTP {r.status_code} {r.text[:200]}")
            return None
        data = r.json().get("data", r.json())
        return data.get("state") or data.get("status")
    except Exception as e:
        log(f"API 查状态异常: {e}")
        return None


def _extract_action_url(payload, raw_text=""):
    """从 API 响应里找 action_url（唤醒页链接）。字段可能在顶层、
    data 里或 error 里；都找不到就用正则在原文里捞一个
    falixnodes.net/s/... 链接。找不到返回 None。"""
    def _pick(obj):
        if isinstance(obj, dict):
            v = obj.get("action_url")
            if isinstance(v, str) and v.startswith("http"):
                return v
            for key in ("data", "error"):
                found = _pick(obj.get(key))
                if found:
                    return found
        return None
    found = _pick(payload) if isinstance(payload, dict) else None
    if found:
        return found
    m = re.search(r"https://falixnodes\.net/s/[A-Za-z0-9]+", raw_text or "")
    return m.group(0) if m else None


def api_start(api_key, server_id):
    """发一次 start，返回 (HTTP 状态码, action_url)。
    免费服会返回 403 verification_required，响应体里带当次的
    action_url（唤醒页链接，每次都由 API 给、不要写死）；
    200/202 是直接接受，409 是已经在运行。异常返回 (None, None)。"""
    if not api_key:
        return None, None
    try:
        r = requests.post(
            f"{API_BASE}/servers/{server_id}/power",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"signal": "start"},
            timeout=15,
        )
        log(f"API start 返回: HTTP {r.status_code} {r.text[:200]}")
        action_url = None
        if r.status_code == 403:
            try:
                action_url = _extract_action_url(r.json(), r.text)
            except Exception:
                action_url = _extract_action_url(None, r.text)
            if action_url:
                log(f"API 返回的唤醒页链接: {action_url}")
        return r.status_code, action_url
    except Exception as e:
        log(f"API start 异常: {e}")
        return None, None


def api_console_log(api_key, server_id, lines=40):
    """GET /servers/{id}/console/log，返回日志行列表，失败返回 []。

    2026-11-15 实测：这个接口返回 /logs/latest.log 的尾巴（默认 40 行），
    AFK 验证链接就在里面，例如：
    [00:31:20] [Server] Are you still there? Verify within 30 min:
    https://client.falixnodes.net/verify?t=VFKUE9YC
    每 3 分钟还会再发一条倒计时提醒，所以 30 分钟窗口内日志里一定有它。
    """
    if not api_key:
        return []
    try:
        r = requests.get(
            f"{API_BASE}/servers/{server_id}/console/log",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"lines": lines},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"API 读控制台日志失败: HTTP {r.status_code} {r.text[:200]}")
            return []
        data = r.json().get("data", r.json())
        return data.get("lines") or []
    except Exception as e:
        log(f"API 读控制台日志异常: {e}")
        return []


AFK_URL_RE = re.compile(r"https://client\.falixnodes\.net/verify\?t=[A-Za-z0-9]+")


# AFK 成功页的可见文字关键词（只认可见文字，不认页面源码——
# 2026-11-15 00:53 的教训：源码里本来就有 success 这类词）。
# 注意 "keep running" 不能加：点按钮前的页面就有
# "Complete the captcha below to keep your server running."。
AFK_SUCCESS_WORDS = (
    "verified", "success", "thank you", "confirmed",
    "already verified", "all set", "thanks for verifying",
)


def find_afk_verify_url(api_key, server_id, manual_verify_url=""):
    """从控制台日志里找最新的 AFK 验证链接，找不到返回 None。

    manual_verify_url（批量里每行第 3 个字段，或单账号的 VERIFY_URL）
    优先于日志；手动跑一次时把游戏里那条链接填进去就行。
    """
    manual = (manual_verify_url or "").strip()
    if manual:
        return manual
    lines = api_console_log(api_key, server_id)
    for line in reversed(lines):
        m = AFK_URL_RE.search(line or "")
        if m:
            log(f"控制台日志里找到 AFK 验证链接（token 已脱敏）：{m.group(0).split('t=')[0]}t=***")
            return m.group(0)
    return None


# ---- Turnstile 辅助（概念参照 Auto-Renew-Bothosting，代码为本项目重写） ----

TOKEN_JS = """
try {
  if (window.turnstile && window.turnstile.getResponse) {
    var t = window.turnstile.getResponse();
    if (t) return t;
  }
} catch (e) {}
var el = document.querySelector('input[name="cf-turnstile-response"]');
return el ? (el.value || '') : '';
"""

EXISTS_JS = """
return document.querySelector(
  'input[name="cf-turnstile-response"], iframe[src*="challenges.cloudflare.com"], .cf-turnstile'
) !== null;
"""

COORDS_JS = """
function findWidget(root) {
  var els = root.querySelectorAll(
    'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id*="cf-chl"]');
  for (var i = 0; i < els.length; i++) {
    var r = els[i].getBoundingClientRect();
    if (r.width > 50 && r.height > 20) return els[i];
  }
  var all = root.querySelectorAll('*');
  for (var j = 0; j < all.length; j++) {
    if (all[j].shadowRoot) {
      var f = findWidget(all[j].shadowRoot);
      if (f) return f;
    }
  }
  return null;
}
var hit = findWidget(document);
if (!hit) {
  var el = document.querySelector('input[name="cf-turnstile-response"]');
  var cur = el ? el.parentElement : null;
  for (var k = 0; k < 6 && cur; k++) {
    var rr = cur.getBoundingClientRect();
    if (rr.width > 100 && rr.width < 600 && rr.height > 30 && rr.height < 140) { hit = cur; break; }
    cur = cur.parentElement;
  }
}
if (!hit) return null;
var hr = hit.getBoundingClientRect();
return {cx: Math.round(hr.x + 30), cy: Math.round(hr.y + hr.height / 2)};
"""


def js(sb, script, default=None):
    try:
        result = sb.execute_script(script)
        return default if result is None else result
    except Exception:
        try:
            return sb.cdp.evaluate("(function(){ " + script + " })()")
        except Exception:
            return default


def turnstile_token(sb):
    token = js(sb, TOKEN_JS, "")
    return token if isinstance(token, str) else ""


def wait_turnstile(sb, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js(sb, EXISTS_JS, False):
            return True
        time.sleep(1)
    return False


def diag_turnstile_dom(sb):
    """定位失败时打印现场诊断（iframe / turnstile 元素 / window.turnstile），
    方便在 Actions 日志里判断是没加载出来还是没定位到。照搬 eooce 的做法。"""
    snap = js(sb, """
        var out = [];
        var ifr = document.querySelectorAll('iframe');
        out.push('top-doc iframes: ' + ifr.length);
        for (var i = 0; i < ifr.length; i++) {
            var r = ifr[i].getBoundingClientRect();
            out.push('iframe w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' src=' + (ifr[i].src || '').slice(0, 90));
        }
        var ts = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"], input[name="cf-turnstile-response"]');
        out.push('turnstile-ish elements: ' + ts.length);
        out.push('window.turnstile=' + (typeof window.turnstile) + ' readyState=' + document.readyState);
        return out.join('\\n');
    """, "")
    for line in str(snap).splitlines():
        log("   [diag] " + line)


def get_current_ip(proxy_server=""):
    """打印当前出口 IP（走代理时用代理测），方便在日志里确认到底用的哪个 IP。
    照搬 eooce：api.ip.sb/ip。"""
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    # api.ip.sb 走代理时偶发 SSL EOF，换两个站再试，都失败才是代理真不通。
    for url in ("https://api.ip.sb/ip", "https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = requests.get(url, proxies=proxies, timeout=15)
            r.raise_for_status()
            return r.text.strip()
        except Exception as e:
            last_err = e
            continue
    log(f"获取出口 IP 失败: {last_err}")
    return ""


def _activate_window():
    """xdotool 点之前先把 Chrome 窗口激活，否则在 Xvfb 里点击可能落到别的窗口。
    照搬 eooce：这是我们旧代码缺的最关键一步。"""
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", cls],
                capture_output=True, text=True, timeout=3,
            )
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wids[0]],
                    timeout=3, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(
            ["xdotool", "getactivewindow", "windowactivate"],
            timeout=3, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def os_click(x, y):
    """系统级真实鼠标点击（Linux 用 xdotool）。
    注意：x, y 必须是屏幕绝对坐标，不是页面视口坐标。"""
    _activate_window()
    try:
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(x), str(y)],
            timeout=3, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def click_turnstile(sb):
    coords = js(sb, COORDS_JS, None)
    if not coords:
        log("无法定位 Turnstile 坐标，打印现场诊断：")
        diag_turnstile_dom(sb)
        return False
    # eooce 的关键修正：getBoundingClientRect 给的是视口坐标，xdotool 要的是
    # 屏幕绝对坐标，必须加上 window.screenX/screenY 再加上浏览器顶栏高度
    # （outerHeight - innerHeight）。旧代码直接拿视口坐标去点，点的位置是偏的。
    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(coords["cx"]) + sx
        ay = int(coords["cy"]) + sy + bar
    except Exception:
        ax, ay = int(coords["cx"]), int(coords["cy"])
    log(f"真实鼠标点击 Turnstile ({ax}, {ay})")
    if os_click(ax, ay):
        return True
    try:
        sb.cdp.gui_click_x_y(int(coords["cx"]), int(coords["cy"]))
        return True
    except Exception as e:
        log(f"CDP 点击失败: {e}")
    try:
        sb.cdp.click_captcha()
        return True
    except Exception as e:
        log(f"click_captcha 兜底失败: {e}")
    return False


def challenge_load_failed(sb):
    """挑战根本没加载出来时点也没用：页面出现加载失败文案就直接返回。"""
    try:
        text = sb.get_page_source() or ""
    except Exception:
        return False
    return ("验证码加载失败" in text) or ("Captcha failed to load" in text)


def solve_turnstile(sb, timeout=90):
    if turnstile_token(sb):
        log("Turnstile 已通过（已有 token）")
        return True
    # 先等 20 秒让 Managed Turnstile 自己过：IP/浏览器可信时它根本不用点，
    # 盲目立刻点反而可能被判定为机器人。20 秒内拿到 token 就直接返回。
    #
    # 2026-11-15 修正：旧代码一看到“挑战加载失败”就立刻 return False，
    # 还写“不是点法问题，是 IP 被拦了，点也没用”——但日志自己打脸了：
    # 后面兜底点一次就拿到了 token。所以那行字可能是 widget 还没加载完
    # 的瞬时状态（走代理时加载慢），也可能是点一下触发了重载。
    # 现在看到这行字只记一次、不立刻退出，等满 90 秒都没 token 再下结论。
    deadline = time.time() + timeout
    auto_deadline = time.time() + 20
    load_failed_seen = False
    while time.time() < auto_deadline:
        if turnstile_token(sb):
            log("Turnstile 已通过（自动通过，无需点击）")
            return True
        if challenge_load_failed(sb) and not load_failed_seen:
            load_failed_seen = True
            log("Turnstile 显示“挑战加载失败”，先不等死：可能是还没加载完，继续等")
            debug_shot(sb, "turnstile_load_failed")
        time.sleep(2)
    next_click = time.time()
    clicks = 0
    while time.time() < deadline:
        if turnstile_token(sb):
            log("Turnstile 已通过（已获取 token）")
            return True
        if time.time() >= next_click:
            clicks += 1
            log(f"第 {clicks} 次点击 Turnstile 验证框...")
            click_turnstile(sb)
            next_click = time.time() + 10 + random.uniform(0, 3)
        time.sleep(2)
    if challenge_load_failed(sb):
        log("等满 90 秒仍是“挑战加载失败”，这次大概率是 IP/环境被 Cloudflare 拦了")
    else:
        log("等满 90 秒仍没拿到 Turnstile token")
    debug_shot(sb, "turnstile_timeout")
    return bool(turnstile_token(sb))


def _wait_for_running(api_key, server_id, timeout_s=120):
    """等服务器状态变为 online/starting/running，最多等 timeout_s 秒。
    返回 0 成功、1 超时。"""
    for i in range(timeout_s // 10):
        time.sleep(10)
        state = api_status(api_key, server_id)
        log(f"启动后第 {(i + 1) * 10}s，状态: {state}")
        if state in ("online", "starting", "running"):
            notify(f"✅ Falix {server_id} 已启动，状态: {state}")
            log("启动成功")
            return 0
    log(f"等待 {timeout_s} 秒后状态仍未变为 online/starting，请手动检查")
    notify(f"❌ Falix {server_id} 离线，唤醒失败：等待 {timeout_s} 秒后仍未上线")
    return 1


def run_one_account(acct, sb_kwargs):
    """跑一个账号（一个服）的一轮：查状态 → 在线查 AFK / 离线做唤醒。
    每个账号独立开浏览器，互不串状态；返回 0 成功、1 失败或需人工处理。"""
    api_key = acct["api_key"]
    server_id = acct["server_id"]
    label = acct["label"]
    log("=" * 40)
    log(f"{label} 开始：服务器 {server_id}")
    log("=" * 40)

    state = api_status(api_key, server_id)
    # afk_url 不为空时走 AFK 验证模式（服务器在线时才有意义），
    # 为空时走原来的唤醒模式（服务器离线/storage 时）。
    afk_url = None
    if state:
        log(f"当前状态: {state}")
        # 2026-11-14 实测：长期离线后状态会变成 storage（node_ready false，
        # 提示 Server is still being transferred），不再是 offline。
        # 旧代码只认 offline，storage 会直接退出、什么都不做，这是个 bug。
        if state not in ("offline", "storage"):
            # 服务器在线：不唤醒，但要查控制台日志里有没有 AFK 验证。
            # 2026-11-15 实测：GET /console/log 能读到
            # "Are you still there? Verify within 30 min: .../verify?t=..."
            afk_url = find_afk_verify_url(api_key, server_id, acct["verify_url"])
            if not afk_url:
                log("服务器在线，控制台日志里没有 AFK 验证链接，无需处理")
                return 0
            log("服务器在线，但有 AFK 验证待处理")
        if state == "storage":
            log("状态是 storage（可能正在转移），仍尝试唤醒")
    else:
        log("无法确认 API 状态，继续尝试打开唤醒页")

    target_url = afk_url
    if not target_url:
        # 离线/storage：先发一次 API start。免费服会返回 403，
        # 响应体里的 action_url 就是当次的唤醒页链接（各账号/各服
        # 可能不一样，写死在配置里会过期）；200/202/409 说明不用
        # 再过浏览器，直接等状态就行。
        code, wake_url = api_start(api_key, server_id)
        if code in (200, 202, 204):
            log("API start 已接受，跳过浏览器验证，直接等状态")
            return _wait_for_running(api_key, server_id)
        if code == 409:
            log("API 说服务器已在运行/启动中，直接等状态")
            return _wait_for_running(api_key, server_id)
        target_url = wake_url or ACTION_URL
        if wake_url:
            log(f"唤醒页链接由 API 当次返回: {target_url}")
        else:
            log(f"API 没给唤醒页链接，用默认值: {target_url}")
    log(f"打开页面: {target_url.split('?')[0] if afk_url else target_url}"
        + ("（AFK 验证）" if afk_url else "（唤醒）"))
    with SB(**sb_kwargs) as sb:
        sb.open(target_url)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)

        def visible_text():
            try:
                return (sb.get_text("body") or "").strip()
            except Exception:
                try:
                    return (sb.get_text() or "").strip()
                except Exception:
                    return ""

        if not wait_turnstile(sb):
            vt = visible_text()
            log(f"未检测到 Turnstile，页面可见文字: {vt[:300]!r}")
            if afk_url and any(k in vt.lower() for k in AFK_SUCCESS_WORDS):
                # AFK 页没有验证框，但可见文字已经是成功页：
                # 这条链接之前已经验证过了（或已过期但服务器还在跑），
                # 不用再点一次，直接按成功处理。
                log("AFK 页无 Turnstile，但可见文字显示已验证过，按成功处理")
                post_state = api_status(api_key, server_id)
                log(f"AFK 验证后 API 复查状态: {post_state}")
                return 0
            log("未检测到 Turnstile，页面可能已变化，请手动检查")
            debug_shot(sb, f"no_turnstile_{server_id}")
            notify(f"❌ Falix {server_id} "
                   + ("AFK 验证失败：未检测到 Turnstile" if afk_url
                      else "离线，唤醒失败：未检测到 Turnstile"))
            return 1

        if not solve_turnstile(sb):
            # eooce 的兜底：失败后再调一次 click_captcha，4 秒后复查 token。
            log("Turnstile 验证失败，尝试兜底点击一次...")
            try:
                sb.cdp.click_captcha()
                sb.sleep(4)
            except Exception as e:
                log(f"兜底点击失败: {e}")
            if not turnstile_token(sb):
                log("Turnstile 验证失败，停止（不反复重试）")
                notify(f"❌ Falix {server_id} "
                       + ("AFK 验证失败：Turnstile 未通过" if afk_url
                          else "离线，唤醒失败：Turnstile 未通过"))
                return 1
            log("兜底点击后拿到 token，继续")

        if afk_url:
            # AFK 验证页：Turnstile 通过后必须真的点 Verify/提交按钮，
            # 再看页面上可见的成功文案才算完成。
            #
            # 2026-11-15 00:53 的教训：旧代码用 get_page_source() 查
            # "success" 就判成功，结果服务器 01:01:54 还是停了——
            # 页面源码里（JS bundle）本来就含有 success 这类词，
            # 还没点 Verify 就误判了。所以成功判定只认可见文字
            # （sb.get_text），而且先点按钮、再轮询可见文字确认。
            sb.sleep(3)
            vt = visible_text()
            log(f"AFK 验证页可见文字: {vt[:300]!r}")
            clicked = False
            # 2026-11-15 12:27 实测：AFK 页的按钮文案是 "✓ I'm here!"，
            # 不在 Verify/Complete/Confirm 候选里，白白各等了 10 秒，
            # 最后是 button[type="submit"] 点上的。把真实文案放第一位。
            for selector in (
                "button:contains(\"I'm here\")",
                'button:contains("Verify")',
                'button:contains("Complete")',
                'button:contains("Confirm")',
                'button[type="submit"]',
            ):
                try:
                    sb.click(selector, timeout=10)
                    log(f"已点击 AFK 验证按钮（{selector}）")
                    clicked = True
                    break
                except Exception as e:
                    log(f"AFK 验证按钮点击失败（{selector}）: {e}")
            if not clicked:
                # 没找到按钮：可能是 token 一通过就自动提交了，
                # 也可能是按钮文案不在候选里。看可见文字再定。
                log("没找到 AFK 验证按钮，检查可见文字是否已显示成功")
            # 2026-11-15 12:27 实测：成功页的可见文字是
            # "You're all set! Thanks for verifying. Your server will keep running."
            # 旧关键词一个都对不上（"verifying" ≠ "verified"，"Thanks for verifying"
            # ≠ "thank you"），点了按钮也判失败、白白 exit 1。
            for _ in range(15):
                vt = visible_text()
                if any(k in vt.lower() for k in AFK_SUCCESS_WORDS):
                    log(f"AFK 验证已完成（可见文字）: {vt[:200]!r}")
                    # 独立复查：页面说通过了，再用 API 确认服务器还在运行
                    post_state = api_status(api_key, server_id)
                    log(f"AFK 验证后 API 复查状态: {post_state}")
                    notify(f"✅ Falix {server_id} AFK 验证已完成，服务器继续运行")
                    return 0
                time.sleep(2)
            log(f"AFK 验证未确认成功（已点按钮: {clicked}），最后可见文字: {vt[:300]!r}")
            debug_shot(sb, f"afk_not_confirmed_{server_id}")
            notify(f"⚠️ Falix {server_id} AFK 验证未确认成功，请手动检查验证页")
            return 1

        # 验证通过后点启动。唤醒页是 JS 渲染的，公开抓取看不到按钮
        # 的确切文案（2026-11-12 实测页面只露出 "Asleep" 和步骤文案），
        # 所以按多个候选文案依次尝试，而不是只认一种写法。
        start_clicked = False
        for selector in (
            'button:contains("Start the server")',
            'button:contains("Start")',
            'button[type="submit"]',
        ):
            try:
                sb.click(selector, timeout=10)
                log(f"已点击启动按钮（{selector}）")
                start_clicked = True
                break
            except Exception as e:
                log(f"启动按钮点击失败（{selector}）: {e}")
        if not start_clicked:
            log("所有启动按钮候选都点不动，页面可能已变化，请手动检查")
            debug_shot(sb, f"wake_no_button_{server_id}")
            notify(f"❌ Falix {server_id} 离线，唤醒失败：启动按钮点不动")
            return 1

        sb.sleep(5)
        # 验证通过 5 分钟内，用 API 再确认 / 补发一次 start
        api_start(api_key, server_id)
        return _wait_for_running(api_key, server_id)


def main():
    log("=" * 40)
    log("Falix 自动唤醒（多账号）")
    log("=" * 40)

    if SB is None:
        log("缺少 seleniumbase，请先 pip install seleniumbase requests")
        return 1

    accounts = parse_accounts()
    if not accounts:
        log("没有配置任何账号：填 FALIX_BATCH（批量）或 FALIX_API_KEY（单账号）")
        return 1
    log("共 {} 个账号：{}".format(
        len(accounts), "、".join(a["label"] for a in accounts)))

    # 代理所有账号共用：只起一次。本地先跑 setup_proxy.py；
    # GitHub Actions 里 workflow 已经先跑过，这里会检测到 1080 已占用并跳过。
    global PROXY_SERVER
    if NODE_LINK and not PROXY_SERVER:
        log("只配置了 NODE_LINK，尝试用 setup_proxy.py 转本地代理...")
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            r = subprocess.run(
                [sys.executable, os.path.join(here, "setup_proxy.py")],
                timeout=120, capture_output=True, text=True,
            )
            log(r.stdout[-1000:] if r.stdout else "")
            if r.returncode == 0:
                PROXY_SERVER = "http://127.0.0.1:1080"
        except Exception as e:
            log(f"setup_proxy.py 调用失败，继续直连: {e}")

    sb_kwargs = {"uc": True, "headless": HEADLESS, "locale_code": "en-US"}
    if PROXY_SERVER:
        # 只打印协议和 host，不打印可能含密码的完整代理地址
        log(f"使用代理: {PROXY_SERVER.split('@')[-1]}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        log("未使用代理，直连访问（机房 IP 可能过不了 cf 盾）")

    # 先打印出口 IP：日志里能确认到底走的哪个 IP，失败时也好换节点复盘。
    ip = get_current_ip(PROXY_SERVER if PROXY_SERVER else "")
    if ip:
        log(f"当前出口 IP: {ip}")

    results = {}
    for i, acct in enumerate(accounts):
        if i > 0:
            # 账号之间隔 5 秒：等上一个浏览器退干净再开下一个
            # （kataBump 同款做法），也避免请求贴得太紧。
            time.sleep(5)
        try:
            results[acct["label"]] = run_one_account(acct, sb_kwargs)
        except Exception as e:
            log(f"{acct['label']} 出现未捕获异常: {e}")
            notify(f"⚠️ Falix {acct['server_id']} 处理异常：{e}，请看日志")
            results[acct["label"]] = 1
    log("=" * 40)
    log("本轮汇总：" + "、".join(
        f"{k}={'成功' if v == 0 else '失败/待处理'}" for k, v in results.items()))
    failed = [k for k, v in results.items() if v != 0]
    if failed:
        notify("⚠️ Falix 多账号本轮未全部成功：" + "、".join(failed) + "，请看日志")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
