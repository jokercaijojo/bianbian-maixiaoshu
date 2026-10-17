# 百变麦小薯 · 会变身的麦当劳优惠蹲点机器人（Maimai Watchdog）

> 一只住在你微信里的「优惠蹲点员」：你只管说爱吃什么，它 7×24 替你盯着麦当劳官方优惠，
> 蹲到了第一时间**图文播报**推到你微信，还能一句话帮你把券领进卡包。
> 她还会「变身」——说一句「变身 月野兔」，她就变成你爱的那个动漫角色陪你聊。

麦当劳程序员节创意开发大赛参赛作品 · 基于[麦当劳官方 MCP](https://open.mcd.cn/mcp) 构建 · 全程使用腾讯 WorkBuddy 开发

---

## 实际效果（微信实测截图）

**蹲到优惠 → 图文播报（文字 + 官方券图）：**

![优惠图文播报](docs/images/wechat-push.png)

**微信智能问答：不会瞎猜你在哪，而是引导你说出城市和商圈，再调 MCP 查实时报价：**

![微信智能问答](docs/images/wechat-chat.png)

> 截图中涉及个人位置与门店详址的部分已裁剪处理。

## 它解决什么问题？

麦当劳官方的优惠券 / 活动其实很香（单品买一送一、免费脆薯饼、9.9 早餐两件套……），
但**没人有功夫天天刷 App 看有没有上新**，等刷到的时候券常常已经没了。

「麦小薯」把这件事自动化了：

- 👀 **精准蹲点**：只盯你关注的产品（麦香鱼、薯条、板烧……），有优惠立刻推送到微信，别的噪音一律不打扰
- 🖼️ **图文播报**：推送直接带官方优惠券配图，券长什么样、划不划算，一眼看清
- 🎟️ **一键领券**：微信里回一句「领券」，当前所有可领优惠券立刻塞满你的卡包
- 💬 **随问随答**：「麦香鱼多少钱」「轻轨站附近有店吗」「我多少积分」——
  它通过 Function Calling 实时调用官方数据回答，不用打开 App
- 🍟 **有活人感**：它有名字、有口癖、会报喜会卖萌，蹲点失败了会自己去"后厨"想办法
- 🎭 **会变身**：说「变身 月野兔」就变成你爱的那个角色，语气口癖全对上，还能随时「变回来」
- 🎴 **会打卡换装**：每天聊满两轮算打卡，累计解锁新造型卡，集齐 12 张皮肤
- 🧠 **会记事**：你说过的忌口、爱喝什么、希望怎么被称呼，聊过的她都记得

## 目标用户

- 爱吃麦当劳、但懒得天天刷 App 找优惠的人
- 只想蹲特定单品优惠（比如只爱麦香鱼），不想被全量营销信息轰炸的人
- 微信重度用户：所有交互都在微信对话里完成，不用装任何新 App

## 系统架构

```
┌──────────────┐   微信对话    ┌─────────────────────────────────────┐
│   用户微信    │ ◀──────────▶ │  微信 ClawBot 通道（iLink 长轮询，    │
│ （ClawBot）   │   图文消息    │  无需公网 IP，支持文字+图片推送）      │
└──────────────┘              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │        麦小薯大脑             │
                              │  · 指令路由（关注/取消/领券…） │
                              │  · GLM-5.3-Flash 工具代理      │
                              │    代理（自主决策调 MCP 工具） │
                              │  · 定时扫描 + 关键词命中去重   │
                              └──────┬───────────────┬──────┘
                                     │ MCP (HTTP/SSE) │ LLM API
                       ┌─────────────▼──────┐   ┌─────▼──────┐
                       │  麦当劳官方 MCP      │   │  智谱 GLM   │
                       │  mcp.mcd.cn         │   │  glm-5.3   │
                       └────────────────────┘   └────────────┘
┌──────────────┐
│  FastAPI 网站 │  落地页 / MCP Token 申请向导 / 关注配置 / 扫码绑定
└──────────────┘
```

## 快速开始

### 前置条件

- Python 3.10+，Node.js（仅用于 `npm run dev` 启动网站）
- 麦当劳 MCP Token：到 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 注册并创建（免费，需本人手机号）
- 智谱 AI API Key（可选，用于自由聊天与智能查价）：[open.bigmodel.cn](https://open.bigmodel.cn)

### 安装

```bash
git clone <your-repo-url>
cd mcd-coupon-watchdog
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 配置（只用环境变量，仓库零密钥）

```bash
cp .env.example .env
# 编辑 .env，填入：
#   MCD_MCP_TOKEN=你的麦当劳MCP Token
#   ZHIPU_API_KEY=你的智谱API Key（可选）
#   ZHIPU_MODEL=glm-5.3-flash
```

MCP 配置模板见 [`mcp-config.example.json`](./mcp-config.example.json)。

### 运行

```bash
# 1. 微信扫码绑定 ClawBot（首次，凭证存本地 claw_credentials.json，已 gitignore）
python scripts/bind_wechat.py

# 2. 启动常驻服务（微信收发 + 每 30 分钟蹲点扫描 + 图文播报）
python scripts/daemon.py

# 3. 启动网站（落地页 / Token 向导 / 扫码绑定页）
npm run dev -- --port 7100
```

### 微信里的用法

| 你发 | 麦小薯回 |
|---|---|
| `关注 麦香鱼` | ✅ 已关注，有优惠立刻喊你 |
| `查券` | 立刻扫一遍当前可领券+活动，命中即图文播报 |
| `领券` | 一键把全部可领券领进你的卡包 |
| `麦香鱼多少钱？我在中山轻轨站附近` | 自主调 MCP：找店→查菜单→算价，报真实价格 |
| `清单` / `取消 薯条` / `帮助` | 管理你的关注清单 |

## 项目结构

```
mcd-coupon-watchdog/
├── src/maimai_watchdog/
│   ├── mcp_client.py    # 麦当劳 MCP 客户端（JSON-RPC + SSE）
│   ├── monitor.py       # 蹲点核心：解析券/活动+配图、关键词命中
│   ├── agent.py         # GLM Function Calling 代理（9 个 MCP 工具）
│   ├── chat.py          # 微信指令路由
│   ├── channel_claw.py  # 微信 iLink 通道（文字+图片推送）
│   ├── webapp.py        # FastAPI 网站后端
│   ├── static/          # 落地页 + 开通向导
│   └── db.py            # SQLite：用户(加密Token)/规则/去重/配额/绑定码
├── scripts/
│   ├── daemon.py        # 常驻服务
│   └── bind_wechat.py   # 微信扫码绑定
├── assets/avatar/           # 角色形象母版原图（4 竖版 + 4 微信方图）
├── CONTEST_DECLARATION.md   # 参赛声明（官方原文，未改动）
├── MCP_INTEGRATION.md       # MCP 接入说明
└── workbuddy.md             # WorkBuddy 开发对话记录（系统开发 + 形象制作）
```

## 安全与合规

- 所有 Token / API Key 仅通过环境变量注入，仓库内零明文密钥
- 用户 MCP Token 在数据库中以 Fernet 对称加密存储
- 写操作（领券）必须由用户明确指令触发，机器人绝不擅自执行
- 微信推送仅发送给用户本人主动绑定的会话
- 项目内不含真实姓名、联系方式与精确地理位置
- 角色形象为原创设计，不含任何品牌标识（详见 [`assets/avatar/README.md`](assets/avatar/README.md)）

## 开源协议

MIT（第三方组件许可见其各自声明：octop-gateway MIT）

---

> 本项目为麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。
