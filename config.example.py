# Falix 自动验证 - 本地配置文件模板
# 用法：复制成 config.py 再填值
#   cp config.example.py config.py
#
# 公库：不要提交 config.py（已进 .gitignore），全部用 GitHub Secrets。
# 私库：可以直接填 config.py 再提交，也可以直接填 app.py 里的 HARDCODED，
#       但填了之后不要再把仓库改成公开。
#
# 这里全部留空，只做占位，不含任何真实密钥。

FALIX_API_KEY = ""   # Falix API Key，需 servers:read + servers:power
# 多账号（2026-11-15 新增）：一行一个服，格式 api_key,server_id
# 填了 FALIX_BATCH 就按批量跑，不填走上面单账号。字段前后多余的单双引号会自动去掉。
# 链接不要写进来：AFK 链接每次从控制台日志现读，唤醒页链接每次从 API 的 403 响应里现取。
# 同一个账号有多个服时，key 重复写多行就行。
FALIX_BATCH = ""
SERVER_ID = ""       # 默认 3564044，留空用默认值
ACTION_URL = ""      # 默认 https://falixnodes.net/s/2dxiy1giuhvyk
NODE_LINK = ""       # 代理链接，如 hysteria2:// / vless:// / vmess:// / tuic:// / anytls:// / ss://，由 setup_proxy.py 转本地代理
PROXY_SERVER = ""    # 普通代理，如 http://127.0.0.1:1080；填了 NODE_LINK 一般不用填
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""
HEADLESS = "false"
