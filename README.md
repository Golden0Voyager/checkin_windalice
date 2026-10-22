# checkin_windalice

[![CI](https://github.com/Golden0Voyager/checkin_windalice/actions/workflows/ci.yml/badge.svg)](https://github.com/Golden0Voyager/checkin_windalice/actions/workflows/ci.yml)
[![Daily check-in](https://github.com/Golden0Voyager/checkin_windalice/actions/workflows/checkin.yml/badge.svg)](https://github.com/Golden0Voyager/checkin_windalice/actions/workflows/checkin.yml)

Wind Alice 每日签到自动化。每天北京时间 **08:00 / 21:00** 各跑一次，领到积分就推一条 Bark 到手机。

```
08:00  [跳过] 今天无需领取：今日已领取        ← 21:00 兜底时通常走到这里
08:00  [成功] 已领取 200 积分 → Bark「领取成功 🎉」
08:00  [失败] 凭据已失效（HTTP 401）…        → Bark「签到失败」
```

## 快速开始

```bash
git clone https://github.com/Golden0Voyager/checkin_windalice && cd checkin_windalice
uv sync

# 1. 浏览器里 alice.wind.com.cn 保持登录状态即可，然后一条命令同步凭据 + 到期日
uv run checkin-sync

# 2. 推送密钥（可选，不填就不推送）
gh secret set BARK_KEY --repo <owner>/checkin_windalice

# 3. 手动跑一次验证
gh workflow run checkin.yml --repo <owner>/checkin_windalice
```

看到 `[跳过] 今天无需领取：今日已领取` 就说明通了。

## 它做了什么

Wind Alice 的签到是真实的积分接口，**不是「登录即签到」**：

| 动作 | 接口 | 返回 |
|---|---|---|
| 查状态 | `GET /aggregation/api/v1/points/daily-check-in/status` | `claimable` / `unclaimableReason` / `rewardPoints` |
| 领取 | `POST /aggregation/api/v1/points/daily-check-in/claim` | 到账积分 |

每天 200 积分。执行流程：

1. 查状态 → `claimable: false` → 记一行「今日已领取」，退出码 `0`，**不推送**（不打扰）
2. `claimable: true` → POST 领取 → 推 Bark「领取成功 🎉」
3. 凭据失效 / 接口故障 / 网络异常 → 推 Bark「签到失败」，退出码 `1`
4. 凭据配置缺失或格式错误 → 只打日志，退出码 `2`

**幂等**：08:00 已领过，21:00 那次会走「今日已领取」分支 —— 不会重复领，也不会重复推送。

## 为什么不能「每天自动登录」

Alice 只支持 **手机号 + 短信验证码** 登录，没有账号密码，短信码无法自动化。
所以本项目的思路是 **保住一次登录拿到的长效凭据**，而不是每天重新登录。

认证是三个值的组合：

| 名称 | 来源 | 作用 |
|---|---|---|
| `weaver-auth.refresh-token` | httpOnly cookie | 长效凭据（10 天），网关拿它换 access-token |
| `weaver-auth.session-id` | httpOnly cookie | 会话标识 |
| `wind.sessionid` | 请求头 | 服务端强制要求，缺了直接 `401 no_credentials` |

只要这三个值有效，脚本**完全不需要浏览器**，一个 `GET` + 一个 `POST` 就够。

## 凭据寿命：会被浏览器轮换

两条实测结论决定了整套设计：

- **10 天绝对过期**：cookie 库里 `refresh-token` / `session-id` / `access-token` 的到期时间
  完全一致，都等于「签发时刻 + 10 天」；
- **会轮换**：`access-token` 是 5 小时 JWT，浏览器里它一过期，下次访问就会换一整套新票
  （新 refresh-token + 新 session-id），**旧票当场作废**，同时到期日整体 +10 天。

我们自己的请求**不会**触发轮换（实测响应里 `Set-Cookie` 是 **0 条**）。所以：

> **谁最后换票，谁手里那张才有效。**

你在网页版用一次，云端 Secrets 里那份就失效一次，第二天的定时任务会以
`401 authorization_denied / upstream_rejected` 失败并推 Bark。

这不是代码 bug，是「静态拷贝 + 活跃会话」的结构冲突。应对方式是一条命令重同步 ↓

## 配置

### Secrets（机密）

| 名称 | 必填 | 说明 |
|---|---|---|
| `WIND_REFRESH_TOKEN` | ✅ | 见「重新抓取凭据」 |
| `WIND_SESSION_ID` | ✅ | 同上 |
| `WIND_HEADER_SESSION_ID` | ✅ | 同上 |
| `BARK_KEY` | 可选 | 不填则不推送，签到照常进行 |

### Variables（非机密）

| 名称 | 说明 |
|---|---|
| `WIND_CREDENTIAL_EXPIRES_ON` | 凭据到期日 `YYYY-MM-DD`；剩 2 天时推 Bark 提醒 |
| `BARK_BASE_URL` | 自建 Bark 服务端时使用，默认 `https://api.day.app` |
| `WIND_BASE_URL` | 默认 `https://alice.wind.com.cn`，一般不用改 |

## 重新同步凭据（一条命令）

```bash
uv run checkin-sync
```

它做四件事：从 Chrome 的 cookie 库解出**当前有效**的凭据 → 写入 3 个 Secrets →
更新 `WIND_CREDENTIAL_EXPIRES_ON` → 打一次 status 自检。只会打印长度与哈希，不打印值。

```
[读取] Profile 1  refresh-token sha=6c631f33  到期=2026-10-31
[ok] WIND_REFRESH_TOKEN       len=32
[ok] WIND_SESSION_ID          len=32
[ok] WIND_HEADER_SESSION_ID   len=32
[ok] WIND_CREDENTIAL_EXPIRES_ON = 2026-10-31
[ok] 自检通过：不可领取（今日已领取），奖励 200 积分
```

前提与细节：

- 浏览器里 `alice.wind.com.cn` 是登录状态（cookie 库默认读 `Profile 1`，
  可用 `WIND_CHROME_PROFILE` 指向别的 profile）；
- 首次运行钥匙串会弹窗要「Chrome Safe Storage」，点**始终允许**，以后不再问；
- 只支持 macOS + Chrome（解密依赖 `security` 与 `openssl`，无第三方依赖）。

<details>
<summary>不用命令行 / 没有 Chrome：手动抓（点开）</summary>

1. 浏览器登录 <https://alice.wind.com.cn>
2. `F12` → **Network** → 过滤 `daily-check-in`
3. 点任意一条 `status` 请求 → **Headers** → **Request Headers**
4. 复制 `cookie` 里 `weaver-auth.refresh-token=...` 与 `weaver-auth.session-id=...` 的值
5. `wind.sessionid` 请求头可以直接填第 4 步里的 session-id（实测可行）
6. `gh secret set <名字>` 覆盖对应 Secret（粘贴时**不要**带 `名字=` 前缀）
7. `gh variable set WIND_CREDENTIAL_EXPIRES_ON --body <新到期日>`
8. `gh workflow run checkin.yml` 验证

</details>

> 凭据等价于你的登录态：不要贴进 issue、PR、聊天记录，也不要写进任何文件。

## 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `401` + `credentials_rejected / refresh_token_rejected_cached` | 凭据已失效，或短时间内请求过多触发上游拒绝 | 重新登录并重抓（见上） |
| `401` + `no_credentials / missing_credentials` | 少了 `wind.sessionid` 请求头 | 检查 `WIND_HEADER_SESSION_ID` 是否为空 |
| `403` / 返回 HTML 而非 JSON | 网关风控或地址变了 | 看 `[失败]` 日志里的响应片段，必要时重抓 |
| 签到成功但 Bark 没响 | `BARK_KEY` 未配置或写错 | `gh secret list` 确认；Bark 失败**不影响**签到 |
| 某天完全没跑 | GitHub 会在仓库 60 天无活动后停用 cron | Actions 页面重新 enable 该 workflow |
| 推送显示「0 积分」 | 服务端没返回到账数值，回落值也缺失 | 不影响领取；把日志贴给我即可 |

## 非目标

- **不做**兑换码、技能比赛、新手弹窗等其它领积分入口 —— 只要签到。
- **不做**自动登录 —— 短信验证码无法自动化，见上文。
- **不保存**任何凭据到文件：全部走环境变量 / GitHub Secrets。

## 本地开发

```bash
uv sync
uv run pytest --cov      # 47 个测试，覆盖率阈值 85%
uv run ruff check . && uv run ruff format --check .
uv run mypy src

# 本机从 Chrome 取票并推送到 GitHub Secrets（含自检）
uv run checkin-sync
```

本地跑一次（凭据只用环境变量，不落盘）：

```bash
export WIND_REFRESH_TOKEN=... WIND_SESSION_ID=... WIND_HEADER_SESSION_ID=...
uv run checkin-windalice
echo $?   # 0=正常  1=签到失败  2=配置缺失
```

### 项目结构

```
src/checkin_windalice/
├── _http.py     # 可注入的标准库传输层（HTTP 错误码当正常响应返回）
├── client.py    # 签到接口 + 凭据/错误语义，异常永不回显凭据
├── notify.py    # Bark 推送，失败只返回 False，永不抛异常
├── cli.py       # 编排、退出码、到期提醒
├── chrome.py    # 本机取票：解 Chrome cookie 库（PBKDF2 + AES-128-CBC + openssl）
└── sync.py      # 一条命令把取到的票推回 GitHub Secrets，并自检
tests/           # 全部走假传输层 + 本地 HTTP 服务，不打真网
.github/workflows/
├── ci.yml       # ruff → ruff format → mypy(软失败) → pytest --cov → coverage → 依赖审计
└── checkin.yml  # 每日签到（08:00 / 21:00 北京时间）
```

### 设计取舍

- **零运行时依赖**：只用一个 `GET` + 一个 `POST`，stdlib `urllib` 足够，CI 更好缓存。
- **传输层注入**：测试不 mock `urlopen`，而是换掉整个 transport；真实传输层由本地
  HTTP 服务测试（`tests/test_http.py`）。
- **通知永不影响主流程**：Bark 挂了你照样能领到积分。
- **每日只跑 2 次**：上游对高频请求敏感（见「已知限制」），别加更密的 cron。

## 已知限制

- **私有接口，会变**。Wind 改版或换网关，脚本立刻失效，需要重新逆向。
- **凭据会被浏览器轮换**：你在网页版用一次，云端那份就失效。失败后跑
  `uv run checkin-sync` 即可（一条命令，不用 F12 手抄）。
- **凭据 10 天绝对到期**：即使完全不轮换，也要每 10 天重新登录一次（提醒已内置）。
- **别高频调用**：实测连发十几次请求后上游返回 `refresh_token_rejected_cached`，
  那套凭据当场作废。本脚本每天 2 次，远低于阈值；你在浏览器里的正常使用也不受影响
  （已在 GitHub 的机器上跨环境验证通过）。
- **GitHub cron 有 5~30 分钟随机延迟**，签到对分钟不敏感，不影响结果。
- 仓库公开，但 **GitHub Secrets 不会因公开而泄漏**；代码与测试里只出现假值，
  日志里只打印状态与积分。
