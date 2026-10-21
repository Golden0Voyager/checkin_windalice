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
# 1. 拿凭据（详见「重新抓取凭据」）
#    浏览器登录 alice.wind.com.cn → F12 → Network → daily-check-in/status → Request Headers

# 2. 写进 Secrets
gh secret set WIND_REFRESH_TOKEN   --repo <owner>/checkin_windalice
gh secret set WIND_SESSION_ID      --repo <owner>/checkin_windalice
gh secret set WIND_HEADER_SESSION_ID --repo <owner>/checkin_windalice
gh secret set BARK_KEY             --repo <owner>/checkin_windalice   # 可选

# 3. 记下凭据到期日（登录时刻 + 10 天），供到期前提醒用
gh variable set WIND_CREDENTIAL_EXPIRES_ON --body 2026-10-31 --repo <owner>/checkin_windalice

# 4. 手动跑一次验证
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

## 凭据寿命：10 天（硬上限）

Wind 给登录态设的是 **10 天绝对过期**，不是「用一次续一次」。依据（实测，非推测）：

- Chrome cookie 库里 `weaver-auth.refresh-token` / `session-id` / `access-token` 的
  到期时间完全一致，都等于「登录时刻 + 10 天」；
- 让浏览器正常刷新一次页面后，这个到期时间 **一个微秒都没变**（`delta=+0.0s`），
  说明天天使用也不会续期；
- `access-token` 本身是 5 小时 JWT（`iat`/`exp` 相差 18000 秒），由网关用 refresh-token 隐式换发。

结论：**不存在一劳永逸的方案**，每 10 天要重新登录一次。所以加了到期前提醒 ↓

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

## 重新抓取凭据（每 10 天一次）

> **重新登录会生成一整套全新的 cookie**（新 refresh-token + 新 session-id），旧的那套随即作废。
> 所以顺序必须是「重登 → 立刻重抓 → 立刻覆盖 Secrets」，别拖到下一次定时任务，
> 否则中间那段时间签到必然 `401`。

1. 浏览器登录 <https://alice.wind.com.cn>
2. `F12` → **Network** → 过滤 `daily-check-in`
3. 点任意一条 `status` 请求 → **Headers** → **Request Headers**
4. 复制 `cookie` 里 `weaver-auth.refresh-token=...` 与 `weaver-auth.session-id=...` 的值
5. 复制请求头 `wind.sessionid` 的值
6. `gh secret set <名字>` 覆盖对应 Secret（粘贴时**不要**带 `名字=` 前缀）
7. `gh variable set WIND_CREDENTIAL_EXPIRES_ON --body <新到期日>`
8. `gh workflow run checkin.yml` 验证

> 这三个值等价于你的登录态：不要贴进 issue、PR、聊天记录，也不要写进任何文件。

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
└── cli.py       # 编排、退出码、到期提醒
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
- **凭据 10 天到期**，到期要人工重登一次（提醒已内置）。
- **别高频调用**：实测连发十几次请求后上游返回 `refresh_token_rejected_cached`，
  那套凭据当场作废。本脚本每天 2 次，远低于阈值；你在浏览器里的正常使用也不受影响
  （已在 GitHub 的机器上跨环境验证通过）。
- **GitHub cron 有 5~30 分钟随机延迟**，签到对分钟不敏感，不影响结果。
- 仓库公开，但 **GitHub Secrets 不会因公开而泄漏**；代码与测试里只出现假值，
  日志里只打印状态与积分。
