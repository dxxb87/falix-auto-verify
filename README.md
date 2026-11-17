# Falix qawqaw 自动验证 / 自动唤醒

参照 [eooce/Auto-Renew-Bothosting](https://github.com/eooce/Auto-Renew-Bothosting)（GPL-3.0）
的思路，为 Falix 免费服写的自动验证项目。原项目是给 bot-hosting.net
自动续期用的，这里改成给 Falix 的 qawqaw（服务器 ID 3564044）自动唤醒。

## 原项目到底是怎么做到的

不是什么“万能绕过”，核心就三招：

1. **SeleniumBase UC 模式 + Xvfb 虚拟显示**：用反检测的 Chrome，
   不用 headless，在虚拟桌面里跑，看起来更像真人浏览器。
2. **系统级真实鼠标点击**：定位 Turnstile 控件坐标后，用 `xdotool`
   在操作系统层面点下去（兜底用 CDP 的 `gui_click_x_y` /
   `click_captcha`），而不是 Selenium 的普通点击。
3. **轮询 token 判断是否通过**：反复读
   `window.turnstile.getResponse()` / `input[name="cf-turnstile-response"]`，
   拿到 token 才算通过，90 秒内每约 10 秒点一次。

最关键的一句在原项目 README 里：

> 有 cf 盾，太垃圾的机房节点可能过不了，建议用稍微干净点的节点

也就是说，**成败主要看出口 IP 干不干净**。GitHub Actions 本身就是
机房 IP，不挂干净代理的话，很可能和 Muse 云浏览器一样，点下去就报
“Captcha failed to load. Please try again.”

## 和 Falix 的区别

| | Bot-hosting（原项目） | Falix（本项目） |
|---|---|---|
| 目标 | 登录后点“Renew free plan”续期 | 打开公开唤醒页点“Start the server” |
| 登录 | 需要邮箱 + 密码登录 | **无需登录**，唤醒页明确说无需账户 |
| 验证出现位置 | 登录页 + 续期弹窗里 | 唤醒页里，只有一个 Turnstile |
| 验证后 | 点续期按钮，核对到期日期 | 5 分钟内用 Falix API 再确认 / 补发 `start` |

## 配置：公库 / 私库共用

配置优先级是：**非空环境变量 > `config.py` > `app.py` 里的直写占位 > 默认值**。
环境变量为空字符串时不算已配置，不会把 `config.py` / 直写值顶掉，
所以 workflow 里 Secrets 留空也没关系。

| 方式 | 适合 | 做法 |
|---|---|---|
| **公库：GitHub Secrets** | 仓库公开 | Secrets 里填 `FALIX_API_KEY`、`NODE_LINK` 等，代码里什么都不填 |
| **私库：`config.py`** | 仓库私有（推荐） | `cd github-actions && cp config.example.py config.py`，填好值；`config.py` 已进 `.gitignore`，默认不提交 |
| **私库：直接写代码** | 仓库私有且确定不改公开 | 填 `app.py` 里 `HARDCODED` 那组占位；填了之后不要再把仓库改成公开 |

`config.example.py` 里全部是空占位，不含真实密钥，照着填就行。

## 目录结构（2026-11-16 整理：一个版本一个文件夹）

同一个程序，四种跑法，各住各的文件夹，互不干扰：

| 文件夹 | 跑法 | 里面有什么 |
| --- | --- | --- |
| `github-actions/` | GitHub Actions 定时跑（推荐） | `app.py`、`setup_proxy.py`、`requirements.txt`、`config.example.py`、`.env.example` |
| `docker/` | Docker 容器里跑 | `Dockerfile`、`docker-compose.yml`、`entrypoint.sh` |
| `debian/` | Debian 12 机器上直接装着跑 | `install_debian.sh`、`run_once.sh`、`falix-verify.service` |
| `node/` | 只能跑 Node 的托管平台 | `index.js`、`package.json`、`package-lock.json`、`.env.example` |

`.github/workflows/verify.yml` 必须留在仓库根目录，GitHub Actions 只认这个位置，它跑的是 `github-actions/` 里的程序。根目录只剩总说明 `README.md` 和 `.gitignore`。

## 多账号：FALIX_BATCH（2026-11-15 新增）

一个 Secret 里放多个账号，一行一个服，格式和 kataBump 的 `KATABUMP_BATCH` 一样：

```
api_key_1,3564044
api_key_2,3570001
```

- 每行就两个字段：`api_key,server_id`，别的什么都不用填。
- 两个链接都是每次跑时 API 当场给的，不用写进配置：AFK 验证链接（`verify?t=...`）从各服控制台日志现读，token 每次都不一样；唤醒页链接是离线时先发一次 API start，403 响应体里的 `action_url` 现取，拿不到才用 `ACTION_URL` 默认值兜底。
- 填了 `FALIX_BATCH` 就按批量跑，不填走单账号老配置（`FALIX_API_KEY` + `SERVER_ID`）。
- 每个账号独立开浏览器、独立查状态，账号之间隔 5 秒；代理所有账号共用同一份。
- 同一个账号有多个服时，key 重复写多行就行。
- 字段前后多余的单双引号会自动去掉（贴 `export ...='...'` 时容易把引号一起贴进来，kataBump 就因为这个登不上过）。
- GitHub Actions：在仓库 Secrets 里加一个 `FALIX_BATCH`，workflow 已经会传了；本地跑就设同名环境变量或写 `config.py`。

## 代理：NODE_LINK → sing-box → 本地 1080

浏览器认不出 `hysteria2://...` 这种链接，必须先转成普通代理。
`setup_proxy.py` 就是干这个的（参照原项目用 sing-box 挂 `NODE_LINK` 的思路，
但不拉原项目那个外部 `setup_proxy.sh`，自己写解析 + 只从 GitHub 官方
release 下 sing-box）：

- `NODE_LINK` 是 `hysteria2://` / `vless://` / `vmess://` / `tuic://` /
  `anytls://`（`anttls://` 按同一协议处理）/ `trojan://` / `ss://`：
  解析后生成 `sing-box.json`，启动 sing-box，本地监听 `127.0.0.1:1080`，
  再把 `PROXY_SERVER=http://127.0.0.1:1080` 传给 `app.py`。
- `NODE_LINK` 是普通 `http://` / `socks5://` 代理地址：直接当 `PROXY_SERVER` 用。
- 只填了 `PROXY_SERVER` Secret：直接用，不启动 sing-box。
- 都没填：直连，机房 IP 很可能过不了 cf 盾。

本地也可以先单独试代理转换（不打印密钥，只打印 host:port）：

```bash
cd github-actions
export NODE_LINK="hysteria2://..."
python3 setup_proxy.py --dry-run   # 只解析打印脱敏配置，不下载不启动
python3 setup_proxy.py             # 真正下载 sing-box 并启动本地代理
python3 setup_proxy.py --check     # 检查 1080 端口是否已有代理
```

## 部署（GitHub Actions）

1. 把这个文件夹推到你自己的 GitHub 仓库，开启 Actions。
2. 公库：在仓库 Settings → Secrets and variables → Actions 里加 Secrets。
   私库：也可以不填 Secrets，直接用 `config.py` / 直写（见上表）。

| Secret | 必填 | 说明 |
|---|---|---|
| `FALIX_API_KEY` | 是（除非已直写） | Falix API Key，需 `servers:read` + `servers:power` |
| `NODE_LINK` | 否 | 代理链接，如 `hysteria2://...`，由 sing-box 转本地代理 |
| `SERVER_ID` | 否 | 默认 `3564044` |
| `ACTION_URL` | 否 | 默认 `https://falixnodes.net/s/2dxiy1giuhvyk` |
| `PROXY_SERVER` | 否 | 普通代理地址；填了 `NODE_LINK` 一般不用填 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 否 | Telegram 通知 |

3. 去 Actions 里手动跑一次 `Falix Auto Verify`，看日志。
   默认每 10 分钟跑一次（`verify.yml` 里的 cron），脚本自己会先
   用 API 查状态，不是 offline 就直接退出，不会无意义地点验证。

## 本地跑

```bash
cd github-actions
pip install -r requirements.txt
seleniumbase install chromedriver
cp config.example.py config.py   # 私库：填 config.py；公库：用环境变量
# 或 export FALIX_API_KEY="你的 key" NODE_LINK="hysteria2://..."
xvfb-run --auto-servernum python3 app.py
# app.py 检测到只给了 NODE_LINK 时，会自己先调 setup_proxy.py 转代理
```

## 诚实提醒：不保证能过

- 今天在 Muse 云浏览器里试了多次：验证框能显示出来，点下去还是
  “验证码加载失败，请重试”，启动按钮一直禁用。这个项目换了 UC 浏览器
  + 真实鼠标点击 + 可选干净代理，**比云浏览器更有机会，但不是保证**。
- 如果没有干净的住宅代理，只有 GitHub Actions 的机房 IP，失败概率
  依然很高。先手动跑一次看日志，再决定要不要继续调代理。
- 这是在自动完成 Falix 免费服本来要真人做的验证，存在被 Falix
  判定为滥用、限制账号或服务器的风险。请自行评估，**不要多账号薅**。

## 2026-11-14 研究小结

- **storage 状态是个 bug**：长期离线后服务器状态会从 `offline` 变成 `storage`
  （node_ready false，提示正在转移），旧版 `app.py` 只认 offline，storage
  会直接退出。已修：offline 和 storage 都尝试唤醒。
- **先等 20 秒再点**：Managed Turnstile 在可信环境下会自动过，不用点；
  旧版一上来就点可能适得其反。已修：先等 20 秒自动通过，拿不到 token
  再点；页面出现“验证码加载失败”就直接停，因为挑战根本没加载出来，
  点多少次都没用。
- **决定成败的是 IP，不是点法**：多方资料一致——机房 IP（GitHub Actions、
  云浏览器、VPS）本身就被 Cloudflare 重点盯防，干净的浏览器指纹也救不了
  被标记的 IP；住宅 IP 成功率 70–90%，机房 IP 只有 30–50% 左右。
  Camoufox（Firefox 深度指纹）比 SeleniumBase UC 略强，但同样救不了差 IP。
- **Falix 官方的态度**：官方指南说验证码就是用来“防止机器人刷免费时长”
  的，Timer 页是唯一恢复手段，uptime 保活 ping 没用。自动验证有被判定
  为滥用、限制账号或服务器的风险，自行评估。
- **GitHub 还没连**：2026-11-14 查过，GitHub 是 not_connected，
  想推上去跑得先连 GitHub、选仓库，再填 Secrets（FALIX_API_KEY、
  NODE_LINK/PROXY_SERVER）。

## 2026-11-14 对照 eooce 原项目 + GLaDOS 节点

对照 eooce/Auto-Renew-Bothosting 最新版 `app.py`（已重新 clone 逐行读过），
我们旧代码有三个实打实的差距，已全部修掉：

1. **点之前没激活窗口**：eooce 在 xdotool 点击前先 `xdotool search
   --onlyvisible --class chrome` + `windowactivate --sync`，否则在 Xvfb
   里点击可能落到别的窗口上。旧代码直接 mousemove+click，经常点空。
2. **坐标没换算**：`getBoundingClientRect()` 给的是视口坐标，xdotool 要的是
   屏幕绝对坐标，必须加 `window.screenX/screenY` 再加浏览器顶栏高度
   （`outerHeight - innerHeight`）。旧代码直接拿视口坐标去点，点的位置是偏的。
3. **没搜 shadow DOM**：Turnstile 控件可能藏在 shadowRoot 里，eooce 的定位
   会递归进 shadowRoot 找，旧代码只在 document 里找，找不到就报定位失败。
   另外补了 eooce 的两样：定位失败时打印现场诊断（iframe/turnstile 元素），
   以及开跑前打印当前出口 IP（`api.ip.sb/ip`，走代理时测代理出口），
   失败后多调一次 `click_captcha` 兜底再复查 token。

## 代理订阅已取消（2026-11-15）

以前走过 GLaDOS 的 Mihomo 订阅（`MIHOMO_YAML_URL` / `MIHOMO_NODE`，
订阅里 52 个节点全是 ss + obfs，默认 `US-Fixed-01`），2026-11-15 已取消，
只留 `NODE_LINK` 一条代理链接。`setup_proxy.py` 里的 Mihomo 相关代码、
workflow 里的那两个 Secret、README 里这段的旧接法都已删掉；仓库里
`MIHOMO_YAML_URL` / `MIHOMO_NODE` 两个 Secret 可以删了，留着也没用。
订阅本身不用退订，只是项目里不再用它。

历史实测（留作记录）：在这台云虚拟机上 US-Fixed-01 和 JP-Fixed-01
都没跑通，sing-box 报 `chacha20poly1305: message authentication failed`，
原生 Mihomo 客户端走节点时 HTTPS 也握手失败（订阅服务器解析到
`198.18.245.x` 这段内网段地址，TCP 能通但握手过不去）。

## 部署（Docker / Node 容器）

如果你手头有个能跑 Docker 的 Node 容器（比如 VPS 上的容器、面板里的
Docker 容器），不用走 GitHub Actions，直接用这套：底镜像是
`node:20-bookworm-slim`（自带 Node 20），里面再装 Python + Chrome +
Xvfb + xdotool，跑的还是同一个 `app.py`。`app.py` 本身跑一次就退出，
`entrypoint.sh` 每 30 分钟循环跑一次，代替 Actions 的 cron。

```bash
cp github-actions/.env.example github-actions/.env   # 填 FALIX_API_KEY、NODE_LINK（不要提交真实值）
docker compose -f docker/docker-compose.yml up -d --build
docker logs -f falix-auto-verify   # 看出口 IP 和 Turnstile 结果
```

| 文件 | 作用 |
| --- | --- |
| `docker/Dockerfile` | node:20 + Python 3.11 + Chrome + Xvfb/xdotool + simple-obfs（ss + obfs 链接要靠系统里的 `obfs-local` 才能连上） |
| `docker/entrypoint.sh` | 起 Xvfb -> 起 sing-box 代理 -> 每 `CHECK_INTERVAL` 秒（默认 1800）跑一次 `app.py` |
| `docker/docker-compose.yml` | 一条命令起容器，`shm_size: 2gb` 给 Chrome 用（太小容易崩），`restart: unless-stopped` |
| `github-actions/.env.example` | 配置模板，复制成 `.env` 再填（`.env` 已进 `.gitignore`） |

**先看日志里的两行再定论**：一是 `当前出口 IP`（确认到底走的哪个 IP），
二是 Turnstile 是“加载失败”还是“点了没通过”——前者是 IP/环境被拦，
换节点方向是换更干净的出口；后者才是点法问题。容器网络环境不一样，
第一次跑务必看日志，别默认它能过。

**如果你的容器是只能跑 Node 代码的托管平台**（没有 Docker、装不了 apt、
进不去 shell），这套跑不了，用 `node/` 里的 Node 版（Playwright +
CDP 真实鼠标点击 + 同样的 Turnstile token 轮询，逻辑和 Python 版对齐过）：
`cd node && npm install && npm start`，环境变量在平台里填
（`FALIX_API_KEY` 必填，`PROXY_SERVER` 只支持普通 http/socks5 代理，
需要 sing-box 转换的链接在纯 Node 里接不上，不填就走平台自己的出口 IP）。
服务起来后 `GET /` 看状态，`GET /run` 手动触发一轮。平台得能下载
Chromium（约 170MB）并且让进程常驻，纯 Serverless 跑不了。

## 部署（Debian 12 直接装，不用 Docker）

如果你那台机器就是 Debian 12（bookworm）、有 shell 和 sudo，直接在
上面装 Python + Chrome + Xvfb 跑 Python 版，不用 Docker 也不用 Node 版：

```bash
cd ~/app/falix-auto-verify
bash debian/install_debian.sh   # 装 apt 包、Chrome、Python 依赖，生成 .env
nano github-actions/.env       # 填 FALIX_API_KEY（必填）、NODE_LINK（可选）
bash debian/run_once.sh        # 先手动跑一轮看日志，确认能过验证
```

没问题再挂后台常驻（二选一）：

```bash
# A. 真机/VPS、PID 1 是 systemd 时用（把 debian/falix-verify.service 里的
#    /home/uppcoder 改成你自己的实际路径再复制）：
sudo cp debian/falix-verify.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now falix-verify
sudo journalctl -u falix-verify -f

# B. 容器里没 systemd 时用：
nohup ./docker/entrypoint.sh > verify.log 2>&1 &
tail -f verify.log
```

`docker/entrypoint.sh` 会先起 Xvfb、再起代理（有 NODE_LINK 才起 sing-box），
然后每 30 分钟跑一次 `app.py`。

## 许可证

灵感与思路来自 eooce/Auto-Renew-Bothosting（GPL-3.0），本项目
同样按 GPL-3.0 开源，仅供学习使用。
