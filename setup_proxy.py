#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 NODE_LINK 转成 sing-box 本地代理（127.0.0.1:1080），给 app.py 用。

原项目 Auto-Renew-Bothosting 是用外部 setup_proxy.sh + sing-box 挂 NODE_LINK，
这里改成自己写的 Python 脚本，不拉任何外部脚本，只从 GitHub 官方 release
下载 sing-box 二进制。

用法（本地 / GitHub Actions 都一样）：
  export NODE_LINK="hysteria2://..."   # 或 vless:// / vmess:// / tuic://
                                        # / anytls:// / ss:// / trojan://
  python3 setup_proxy.py               # 启动后 PROXY_SERVER=http://127.0.0.1:1080
  python3 setup_proxy.py --dry-run     # 只解析并打印脱敏后的配置，不下载、不启动
  python3 setup_proxy.py --check       # 只检查 1080 端口是否已有代理在跑

支持的 NODE_LINK 协议：hysteria2、vless、vmess、tuic、anytls、trojan、ss、
socks5、http/https。其中 http/https/socks5 本来就是普通代理地址，不需要
sing-box，脚本会直接把它当 PROXY_SERVER 写进 $GITHUB_ENV（如果在 Actions 里）。
anttls:// 按 anytls 的别名处理。

2026-11-15：Mihomo 订阅（MIHOMO_YAML_URL / MIHOMO_NODE）已取消，订阅那条路
不再用，只留 NODE_LINK 一条代理链接。仓库里那两个 Secret 可以删了。

注意：日志里永远不打印 NODE_LINK / 密码 / uuid，只打印 host:port。
"""
import json
import os
import socket
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

PORT = int(os.environ.get("SING_BOX_PORT", "1080"))
LOCAL_PROXY = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "sing-box.json")
LOG_PATH = os.path.join(HERE, "sing-box.log")
BIN_PATH = os.path.join(HERE, "sing-box")
FALLBACK_VERSION = "1.11.0"


def log(msg):
    print(msg, flush=True)


def mask_host(url):
    """只保留 host:port，去掉密码/uuid 等敏感部分。"""
    try:
        p = urllib.parse.urlparse(url)
        return f"{p.scheme}://{p.hostname}:{p.port}"
    except Exception:
        return "<proxy>"


def port_open(port=PORT, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def write_github_env(key, value):
    path = os.environ.get("GITHUB_ENV")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
        log(f"已写入 $GITHUB_ENV：{key}={value}")


def parse_node_link(link):
    """解析 NODE_LINK，返回 sing-box outbound dict；普通代理返回 None。"""
    p = urllib.parse.urlparse(link.strip())
    scheme = (p.scheme or "").lower()
    q = urllib.parse.parse_qs(p.query)
    first = lambda k, d="": (q.get(k, [d]) or [d])[0]

    if scheme in ("http", "https", "socks", "socks5"):
        return None  # 普通代理，不用 sing-box

    if scheme == "hysteria2":
        password = urllib.parse.unquote(p.username or "")
        tls = {
            "enabled": True,
            "server_name": first("sni", p.hostname or ""),
            "insecure": first("insecure", first("allowInsecure", "0")) in ("1", "true"),
        }
        out = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": p.hostname,
            "server_port": p.port,
            "password": password,
            "tls": tls,
        }
        if first("obfs") == "salamander" and first("obfs-password"):
            out["obfs"] = {"type": "salamander", "password": first("obfs-password")}
        return out

    if scheme == "vless":
        uuid = urllib.parse.unquote(p.username or "")
        out = {
            "type": "vless",
            "tag": "proxy",
            "server": p.hostname,
            "server_port": p.port,
            "uuid": uuid,
            "flow": first("flow", ""),
        }
        if first("security", "tls") == "tls":
            out["tls"] = {
                "enabled": True,
                "server_name": first("sni", p.hostname or ""),
                "insecure": first("allowInsecure", "0") in ("1", "true"),
            }
        net = first("type", "tcp")
        if net == "ws":
            out["transport"] = {
                "type": "ws",
                "path": first("path", "/"),
                "headers": {"Host": first("host", p.hostname or "")},
            }
        return out

    if scheme == "vmess":
        # vmess://base64({"v":"2","add":"host","port":"443","id":"uuid",
        #   "aid":"0","net":"ws","type":"none","host":"","path":"/","tls":"tls","sni":""})
        import base64
        import json as _json

        raw = link[len("vmess://"):].split("#")[0].split("?")[0].strip()
        try:
            cfg = _json.loads(base64.urlsafe_b64decode(raw + "==").decode())
        except Exception as e:
            raise ValueError(f"vmess 链接解析失败（base64 JSON 解不开）：{e}")
        out = {
            "type": "vmess",
            "tag": "proxy",
            "server": cfg.get("add"),
            "server_port": int(cfg.get("port") or 0),
            "uuid": cfg.get("id", ""),
            "alter_id": int(cfg.get("aid") or 0),
            "security": cfg.get("scy") or "auto",
        }
        if (cfg.get("tls") or "") == "tls":
            out["tls"] = {
                "enabled": True,
                "server_name": cfg.get("sni") or cfg.get("add") or "",
                "insecure": False,
            }
        net = (cfg.get("net") or "tcp").lower()
        if net == "ws":
            out["transport"] = {
                "type": "ws",
                "path": cfg.get("path") or "/",
                "headers": {"Host": cfg.get("host") or cfg.get("add") or ""},
            }
        elif net == "grpc":
            out["transport"] = {"type": "grpc", "service_name": cfg.get("path") or ""}
        return out

    if scheme == "tuic":
        # tuic://uuid:password@host:port?congestion_control=bbr
        #   &udp_relay_mode=native&alpn=h3&sni=host
        out = {
            "type": "tuic",
            "tag": "proxy",
            "server": p.hostname,
            "server_port": p.port,
            "uuid": urllib.parse.unquote(p.username or ""),
            "password": urllib.parse.unquote(p.password or ""),
            "congestion_control": first("congestion_control", "bbr"),
            "udp_relay_mode": first("udp_relay_mode", "native"),
            "tls": {
                "enabled": True,
                "server_name": first("sni", p.hostname or ""),
            },
        }
        alpn = first("alpn", "")
        if alpn:
            out["tls"]["alpn"] = [a.strip() for a in alpn.split(",") if a.strip()]
        return out

    if scheme in ("anytls", "anttls"):
        # anytls://password@host:port?sni=host&insecure=1
        # anttls:// 是同一协议的别名写法，按 anytls 处理。
        return {
            "type": "anytls",
            "tag": "proxy",
            "server": p.hostname,
            "server_port": p.port,
            "password": urllib.parse.unquote(p.username or p.password or ""),
            "tls": {
                "enabled": True,
                "server_name": first("sni", p.hostname or ""),
                "insecure": first("insecure", "0") in ("1", "true"),
            },
        }

    if scheme == "trojan":
        return {
            "type": "trojan",
            "tag": "proxy",
            "server": p.hostname,
            "server_port": p.port,
            "password": urllib.parse.unquote(p.username or ""),
            "tls": {
                "enabled": True,
                "server_name": first("sni", p.hostname or ""),
                "insecure": first("allowInsecure", "0") in ("1", "true"),
            },
        }

    if scheme == "ss":
        # ss://method:password@host:port 或 ss://base64(method:password@host:port)
        import base64

        raw = link[len("ss://"):]
        if "@" not in raw.split("?")[0] and "#" not in raw.split("?")[0]:
            try:
                raw = base64.urlsafe_b64decode(raw.split("#")[0].split("?")[0] + "==").decode()
            except Exception:
                pass
        pp = urllib.parse.urlparse("ss://" + raw)
        method, password = (urllib.parse.unquote(pp.username or ""), urllib.parse.unquote(pp.password or ""))
        out = {
            "type": "shadowsocks",
            "tag": "proxy",
            "server": pp.hostname,
            "server_port": pp.port,
            "method": method,
            "password": password,
        }
        # ss:// 链接里的 SIP003 plugin 参数（如 obfs-local），不带它连不上
        # 需要 obfs 的服务器。之前这里直接丢掉了 plugin，是个缺口。
        plugin_q = first("plugin", "")
        if plugin_q:
            parts = [s for s in plugin_q.split(";") if s]
            if parts:
                out["plugin"] = parts[0]
                if len(parts) > 1:
                    out["plugin_opts"] = ";".join(parts[1:])
        return out

    raise ValueError(f"不支持的 NODE_LINK 协议：{scheme}，支持 hysteria2 / vless / vmess / tuic / anytls / trojan / ss / socks5 / http")


def masked_config(cfg):
    """打印用：把密码/uuid 脱敏。"""
    import copy

    c = copy.deepcopy(cfg)
    for o in c.get("outbounds", []):
        for k in ("password", "uuid"):
            if k in o:
                o[k] = "***"
        if "tls" in o and isinstance(o["tls"], dict):
            pass
    return json.dumps(c, indent=2, ensure_ascii=False)


def proxy_self_test():
    """走 127.0.0.1:1080 真实测一次出口 IP，通了返回 IP，否则返回 ''。"""
    for test_url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = subprocess.run(
                ["curl", "-sS", "--proxy", LOCAL_PROXY, "--max-time", "15", test_url],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            log(f"代理自测失败（{test_url}）：{r.stderr.strip()[:200]}")
        except Exception as e:
            log(f"代理自测异常（{test_url}）：{e}")
    return ""


def start_sing_box_with_outbound(outbound):
    """用给定的 outbound 启动 sing-box，监听 127.0.0.1:1080，成功返回 True。"""
    # ss+obfs 的 outbound 要调外部 obfs-local 二进制。
    # 以前这里只检查 1080 端口有没有监听，端口起来了就报“已启动”，其实
    # obfs-local 不存在时 outbound 根本连不上，之后所有流量都会 SSL EOF。
    # 所以先查插件，再做一次真实出口自测，通了才算代理可用。
    import shutil
    plugin = outbound.get("plugin") or ""
    if plugin and not shutil.which(plugin):
        log(f"缺少 obfs 插件 {plugin}，ss+obfs 节点连不上。请先装 simple-obfs："
            f"sudo apt-get install -y simple-obfs（workflow 里已加）")
        return False
    if port_open():
        log(f"端口 {PORT} 已有代理在跑，跳过启动")
        write_github_env("PROXY_SERVER", LOCAL_PROXY)
        return True
    cfg = {
        "log": {"level": "info"},
        "inbounds": [
            {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": PORT}
        ],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        "route": {"final": "proxy"},
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    sing_box = ensure_sing_box()
    log(f"启动 sing-box：{sing_box}，本地代理 {LOCAL_PROXY}")
    with open(LOG_PATH, "ab") as lf:
        subprocess.Popen(
            [sing_box, "run", "-c", CONFIG_PATH],
            stdout=lf, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(15):
        time.sleep(1)
        if port_open():
            break
    else:
        log(f"sing-box 启动后 {PORT} 端口仍未监听，见 sing-box.log")
        return False
    # 端口监听不等于 outbound 能通：走代理真实测一次出口 IP，
    # 两个测 IP 的站都失败就当代理不可用，并把 sing-box 日志尾巴打出来。
    ip = proxy_self_test()
    if ip:
        log(f"sing-box 已启动，本地代理可用：{LOCAL_PROXY}，出口 IP：{ip}")
        write_github_env("PROXY_SERVER", LOCAL_PROXY)
        return True
    log("代理自测失败：端口在监听，但走代理出不去，见 sing-box.log 尾巴：")
    try:
        with open(LOG_PATH, encoding="utf-8", errors="ignore") as lf:
            for line in lf.read().splitlines()[-20:]:
                log(f"   [sing-box] {line}")
    except Exception as e:
        log(f"   [sing-box] 读日志失败：{e}")
    return False


def ensure_sing_box():
    """PATH 或本地有 sing-box 就直接用，否则从 GitHub 官方 release 下载。"""
    import shutil

    found = shutil.which("sing-box") or (BIN_PATH if os.path.exists(BIN_PATH) else None)
    if found:
        return found
    version = FALLBACK_VERSION
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/SagerNet/sing-box/releases/latest",
            headers={"User-Agent": "falix-auto-verify"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            version = json.load(r)["tag_name"].lstrip("v")
        log(f"sing-box 最新版本：{version}")
    except Exception as e:
        log(f"查最新版本失败，用默认版本 {version}：{e}")
    url = f"https://github.com/SagerNet/sing-box/releases/download/v{version}/sing-box-{version}-linux-amd64.tar.gz"
    log(f"下载 sing-box {version} ...")
    tgz = os.path.join(HERE, "sing-box.tar.gz")
    urllib.request.urlretrieve(url, tgz)
    with tarfile.open(tgz) as tf:
        tf.extractall(HERE)
    os.remove(tgz)
    extracted = os.path.join(HERE, f"sing-box-{version}-linux-amd64", "sing-box")
    os.rename(extracted, BIN_PATH)
    os.chmod(BIN_PATH, 0o755)
    return BIN_PATH


def main():
    dry_run = "--dry-run" in sys.argv
    check_only = "--check" in sys.argv

    if check_only:
        log(f"1080 端口 {'已有代理在跑' if port_open() else '没有代理在跑'}")
        return 0 if port_open() else 1

    node_link = os.environ.get("NODE_LINK", "").strip()
    proxy_server = os.environ.get("PROXY_SERVER", "").strip()

    # 没有 NODE_LINK 时：如果本来就有普通代理地址，直接透传，不用 sing-box
    if not node_link:
        if proxy_server:
            log(f"没有 NODE_LINK，直接使用已有代理：{mask_host(proxy_server)}")
            write_github_env("PROXY_SERVER", proxy_server)
            return 0
        log("没有配置 NODE_LINK，也没有 PROXY_SERVER，不启动代理")
        return 0

    if port_open():
        log(f"端口 {PORT} 已有代理在跑，跳过启动")
        write_github_env("PROXY_SERVER", LOCAL_PROXY)
        return 0

    log(f"解析 NODE_LINK：{mask_host(node_link)}")
    try:
        outbound = parse_node_link(node_link)
    except ValueError as e:
        log(f"NODE_LINK 解析失败：{e}")
        return 1

    if outbound is None:
        # http/socks5 普通代理，直接透传
        log(f"NODE_LINK 是普通代理地址，直接使用：{mask_host(node_link)}")
        write_github_env("PROXY_SERVER", node_link)
        return 0

    if dry_run:
        cfg = {
            "log": {"level": "info"},
            "inbounds": [
                {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": PORT}
            ],
            "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
            "route": {"final": "proxy"},
        }
        log("干跑模式，不下载、不启动，生成的 sing-box 配置（已脱敏）：")
        print(masked_config(cfg))
        return 0

    return 0 if start_sing_box_with_outbound(outbound) else 1


if __name__ == "__main__":
    sys.exit(main())
