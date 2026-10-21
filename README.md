# checkin_windalice

Wind Alice 每日签到自动化。每天北京时间 08:00 / 21:00 各跑一次，领到积分就推一条 Bark。

## 它到底做了什么

Wind Alice 的签到是真实的积分接口，不是「登录即签到」：

| 动作 | 接口 |
|---|---|
| 查状态 | `GET /aggregation/api/v1/points/daily-check-in/status` |
| 领取 | `POST /aggregation/api/v1/points/daily-check-in/claim` |

每天 200 积分。脚本流程：

1. 查状态 → `claimable: false` 就记一行「今日已领取」直接退出（退出码 0，不打扰你）
2. `claimable: true` → POST 领取 → 推 Bark「领取成功 🎉」
3. 凭据失效 / 接口故障 / 网络异常 → 推 Bark「签到失败」并以退出码 1 结束

**幂等**：21:00 那次如果 08:00 已经领过，会走「今日已领取」分支，不会重复领、也不会重复推送。

## 为什么不能用「每天自动登录」

Alice 只支持 **手机号 + 短信验证码** 登录，没有账号密码。短信码没法自动化，所以本项目的思路是
**保住一次登录拿到的长效凭据**，而不是每天重新登录。

认证是三个值的组合：

| 名称 | 来源 | 说明 |
|---|---|---|
| `weaver-auth.refresh-token` | httpOnly cookie | 长效凭据，服务网关拿它自动换 5 小时有效的 access-token |
| `weaver-auth.session-id` | httpOnly cookie | 会话标识 |
| `wind.sessionid` | 请求头 | 服务端强制要求，缺了直接 401 |

只要这三个值有效，脚本就完全不需要浏览器。

## 配置 GitHub Secrets

| Secret | 必填 | 说明 |
|---|---|---|
| `WIND_REFRESH_TOKEN` | ✅ | 见下方「重新抓取凭据」 |
| `WIND_SESSION_ID` | ✅ | 同上 |
| `WIND_HEADER_SESSION_ID` | ✅ | 同上 |
| `BARK_KEY` | 可选 | 不填则不推送，签到照常进行 |

```bash
gh secret set WIND_REFRESH_TOKEN --repo <owner>/checkin_windalice
gh secret set WIND_SESSION_ID --repo <owner>/checkin_windalice
gh secret set WIND_HEADER_SESSION_ID --repo <owner>/checkin_windalice
gh secret set BARK_KEY --repo <owner>/checkin_windalice
```

## 重新抓取凭据（凭据过期时）

`weaver-auth.refresh-token` 的寿命由 Wind 决定，过期后签到会以 `401` 失败并推送 Bark。
恢复步骤：

1. 浏览器登录 <https://alice.wind.com.cn>
2. `F12` → **Network** → 过滤 `daily-check-in`
3. 点任意一条 `status` 请求 → **Headers** → **Request Headers**
4. 复制 `cookie` 里的 `weaver-auth.refresh-token=...` 和 `weaver-auth.session-id=...` 的值
5. 复制请求头 `wind.sessionid` 的值
6. `gh secret set <名字>` 覆盖对应 Secret（粘贴时不要带 `名字=` 前缀）
7. 手动触发一次 `Daily check-in` workflow 验证

> 不要把这三个值贴进 issue、PR、聊天记录或任何文件——它们等价于你的登录态。

## 已知限制

- **私有接口，会变**。Wind 改版或换网关，脚本就会失效。
- **别高频调用**。实测：短时间内连发十几次请求后，上游返回
  `kind: credentials_rejected / reason: refresh_token_rejected_cached`，
  那套凭据当场作废，只能重新登录。本脚本每天只跑 2 次，远低于触发阈值；
  你在浏览器里正常使用也不受影响（已在 GitHub 的机器上跨环境验证通过）。
- **GitHub cron 有 5~30 分钟延迟**，且仓库连续 60 天没有提交会自动停用定时任务。
  本项目不靠提交日志跑，所以：如果哪天发现不跑了，先去 Actions 页面点一下 enable。
- **只做签到**。兑换码、技能比赛、新手弹窗等其它领积分入口不在范围内。
- 本仓库是公开的，但 **GitHub Secrets 不会因公开而泄漏**；代码和测试里只用假值，
  日志里也只打印状态与积分。

## 本地开发

```bash
uv sync
uv run pytest --cov
uv run ruff check .
uv run mypy src
```

本地跑一次（凭据走环境变量，不会落盘）：

```bash
export WIND_REFRESH_TOKEN=... WIND_SESSION_ID=... WIND_HEADER_SESSION_ID=...
uv run checkin-windalice
echo $?   # 0 = 正常，1 = 签到失败，2 = 配置缺失
```
