# 鱼鱼找鸡 sidecar（lg找鸡 主路）

给跑在 **Ubuntu 虚拟机**里的 NoneBot 插件提供「本机浏览器直连 Google Lens」的能力。

## 为什么是 sidecar 而不是移植

- bot 在无 GUI 的虚拟机里，起不了浏览器，也就没法直连 Google Lens
- mac 上有真实浏览器，链路已跑通，且**不消耗 SerpApi 的 100 次/月额度**
- 与其把 2324 行 JS 重写成 Python（还要给 VM 装 chromium + playwright），
  不如包一层 HTTP 服务：VM 把图片字节 POST 过来，拿 JSON 回去
- 进程隔离：sidecar 崩了不影响 bot；mac 关机时 Python 侧自动降级到 SerpApi

## 部署（在 mac 上）

### 1. 放好文件

把 `server.mjs` 放到鱼鱼找鸡项目根目录（与 `src/`、`bin/` 同级）。

### 2. 确认 config.json

至少 `browserExe` 要指向本机真实存在的 Chrome：

```json
{
  "engines": ["lens", "saucenao", "tracemoe"],
  "browserExe": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "proxy": "",
  "timeoutMs": 45000,
  "maxAccounts": 3,
  "token": "自己想一个，和 .env 的 LENS_SIDECAR_TOKEN 一致"
}
```

`proxy` 留空即可（mac 本身走 Clash TUN / 系统代理，不要再套一层）。

### 3. 先手动跑一次确认能用

```bash
cd ~/fish-find
FISH_TOKEN=你设的token node server.mjs
```

另开一个终端验证：

```bash
curl http://127.0.0.1:8787/health
curl -X POST -H "x-fish-token: 你设的token" \
     --data-binary @/tmp/test.jpg http://127.0.0.1:8787/find
```

能拿到 `accounts` 就说明通了。

### 4. 开机自启（launchd，推荐）

复制 `fish.find.sidecar.plist` 里的模板，把两处 `/Users/xxx/fish-find` 换成
你的真实路径、token 换成你自己的，然后：

```bash
cp fish.find.sidecar.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/fish.find.sidecar.plist
launchctl list | grep fish.find      # 有输出即已加载
```

plist 里**必须写 `WorkingDirectory`** —— 这是 macOS 上 `uv_cwd` EPERM
（`process.cwd failed`）的根治办法：launchd 启动的进程没有可靠 cwd，
不显式指定就会在 node 启动阶段直接崩。

日志：`/tmp/fish-sidecar.log` 与 `/tmp/fish-sidecar.err`。

### 5. 别让 mac 睡着

```bash
sudo pmset -a sleep 0 displaysleep 10    # 系统不睡，只关屏
```

## bot 侧配置（VM 里的 .env）

```
LENS_SIDECAR_URL=http://192.168.2.45:8787     # mac 的局域网 IP，不要写 127.0.0.1
LENS_SIDECAR_TOKEN=和 mac 上一致
LENS_SIDECAR_TIMEOUT=90                        # 浏览器链路比纯 HTTP 慢
```

SerpApi 那套（`SERPAPI_KEYS` / `LENS_PROXY`）**继续保留**，作为主路不可用时的兜底。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| VM 里 curl 不通 8787 | ① mac 防火墙拦了入站，先临时关防火墙试 ② IP 写错（192.168.2.254 是网关，mac 是 .45）③ 服务没起 |
| 返回 `lens(GOOGLE_CAPTCHA)` | Python 侧会自动降级 SerpApi；要去 mac 上跑一次有头模式养 cookie |
| accounts 为空但 ok=true | 图里没有可判定的社交账号；不会浪费 SerpApi 额度 |
| `CDP 超时：Page.enable` | Chrome 起不来：确认 browserExe 路径正确、没有残留 chrome 进程占用 profile |
