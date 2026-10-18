<p align="center">
  <img src="docs/images/banner.png" alt="百变麦小薯 · 贝雷帽 / 背包 / 薯条 / 挎包 四种造型" width="100%">
</p>

<h1 align="center">百变麦小薯</h1>

<p align="center">
  <b>住在你微信里的专属点餐搭子 · 人设可自定义</b>
</p>

<p align="center">
  <img src="docs/images/badges/python.svg" alt="Python">
  <img src="docs/images/badges/mcp.svg" alt="MCP">
  <img src="docs/images/badges/license.svg" alt="MIT">
  <img src="docs/images/badges/cloudflare.svg" alt="Cloudflare Tunnel">
  <img src="docs/images/badges/workbuddy.svg" alt="WorkBuddy">
</p>

<p align="center">
  🌐 <b>在线体验（真机运行中）</b>：<a href="https://aiunclehouse.com">aiunclehouse.com</a><br>
  抽卡 → 开通 → 微信绑定，三分钟就能领一只自己的麦小薯
</p>

<p align="center">
  <sub>麦当劳程序员节创意开发大赛参赛作品 · 基于<a href="https://open.mcd.cn/mcp">麦当劳官方 MCP</a> 构建 · 全程使用腾讯 WorkBuddy 开发</sub>
</p>

---

## ⚙️ 30 秒看懂技术栈

| 层 | 选型 | 干什么 |
|---|---|---|
| 数据 | **麦当劳官方 MCP**（35 个工具） | 券池/活动日历/找店/查菜单/算价/领券，全是真实接口 |
| 大脑 | **智谱 GLM-5.3-flash** Function Calling | 自主编排「找店→查菜单→算价」工具链 |
| 通道 | **微信 iLink（ClawBot）**· octop-gateway | 长轮询收消息、图文推送、语音转文字 |
| 人设 | SOUL.md + 梗库 + SQLite 长期记忆 | 记得你爱吃什么、在不在减肥 |
| 人设自定义 | **公开百科检索 + GLM 蒸馏** | 换语气/自称/称呼，只取风格不搬台词 |
| 网站 | FastAPI + 原生 HTML/JS（3D 卡柱纯手写 CSS） | 落地页 / 抽卡屋 / 开通向导 / 管理台 |
| 部署 | Cloudflare Tunnel | 一台普通电脑即可公网上线，无需服务器 |

> 💝 **诚意说明**：在线体验站的 AI 对话用的是作者自掏腰包的 GLM API，
> 每位访客每天 100 轮免费额度（防滥用，正常聊天根本用不完）。

---

## 🍟 她是怎样的存在

**🎭 人设，由你决定** —— 说一句「换成元气点的语气」，她的说话方式就跟着换：
元气、沉稳、幽默、温柔都行；想演你喜欢的动漫角色也可以，只学说话风格，不照搬原台词。
看腻了？「变回来」就好。

**👀 优惠，替你蹲着** —— 你只管说爱吃什么，她 7×24 盯官方券池，
麦香鱼一有优惠，微信第一时间**图文播报**（带官方券图），还能一键领进卡包。

**💬 无聊，陪你解闷** —— 吐槽老板、汇报摸鱼、分享离谱事她都接得住。
每天聊满两轮算打卡，累计 1 / 2 / 4 / 6 / 8 天解锁新造型卡。

**🧠 喜好，记在心上** —— 你吃不吃辣、雪糕爱香草还是抹茶、咖啡要冰美式还是热拿铁，
聊过的她都记得，推荐句句量身挑。你随时可以问「你记得我什么」，也可以让她「忘掉 xx」。

---

## 📱 实际效果（微信实测）

**蹲到优惠 → 图文播报（文字 + 官方券图）：**

![优惠图文播报](docs/images/wechat-push.png)

**微信智能问答：不会瞎猜你在哪，而是引导你说出城市和商圈，再调 MCP 查实时报价：**

![微信智能问答](docs/images/wechat-chat.png)

> 截图中涉及个人位置与门店详址的部分已裁剪处理。

---

## 🎴 抽卡屋：把「百变」做成看得见的东西

![抽卡屋](docs/images/gacha.png)

第一次进抽卡屋有新手保护，随手点一张必出薯条妹；之后靠打卡解锁更多造型。
四张卡对应四种工作状态，她会按当前在干什么自动换头像：贝雷帽=闲聊 / 背包=查询 / 薯条=推荐 / 挎包=异常兜底。

<p align="center">
  <img src="docs/images/cards/01_fish_beret.png" width="22%" alt="麦香鱼·贝雷帽">
  <img src="docs/images/cards/02_bigmac_backpack.png" width="22%" alt="巨无霸·双肩包">
  <img src="docs/images/cards/03_fries_hand.png" width="22%" alt="薯条·手捧">
  <img src="docs/images/cards/04_chicken_bag.png" width="22%" alt="麦乐鸡·小挎包">
</p>

<p align="center"><sub>麦香鱼·贝雷帽 ｜ 巨无霸·双肩包 ｜ 薯条·手捧 ｜ 麦乐鸡·小挎包</sub></p>

---

## 🎭 人设自定义：一句话换套说话风格

说一句「换成元气点的语气」，她就换一套说话方式——元气、温柔、幽默、沉稳都由你定。

想演某位动漫角色也可以：她会先去公开百科查条目、列出同名候选让你确认，确认后只提炼
**语气、自称、称呼**这些风格要素，明确不抄录原作台词。以下是本地运行的真实输出：

```text
>>> 用户：换成元气点的语气
<<< 麦小薯：好嘞，「元气」的说话风格我拿捏住了🎭

          【风格名】元气小太阳
          性格
          - 热情开朗，活力满格
          - 乐观，爱给人打气
          - 行动派，不磨叽
          说话风格
          - 语气：明亮上扬，句子短，感叹号常驻
          - 自称：我
          - 称呼：小伙伴 / 你呀
          - 语气词：哇、耶、冲鸭、嘿嘿，兴奋时可叠用

          想让我变回麦小薯，随时发「变回来」～

>>> 用户：今天真的不想上班……
<<< 麦小薯（元气）：哎呀小伙伴，抱抱你呀！🫂
          不想上班太正常啦，谁还没有个周一综合症呢！
          我给你支个招：中午给自己点个开心套餐！随心配 1+1 才 13.9，
          麦辣鸡腿堡 + 麦乐鸡，打工人的充电仪式感直接拉满⚡
          再不行，下班后一份薯条配冰可乐，快乐立刻回归！
          冲鸭，今天就当给周五攒能量啦💪
```

换的只是「说话的调调」，麦当劳的本职永远在线——查价、领券、蹲优惠一个不落。

---

## 🏗️ 架构一览

```text
用户微信 ⇄ 微信 iLink (ClawBot) ⇄ daemon.py（常驻守护）
                                      ├─ 指令层（脚本，毫秒响应）：关注/查券/领券/打卡/变身流程
                                      ├─ GLM 层（自由问答）：SOUL人格 + 长期记忆 + MCP工具链
                                      ├─ 人设引擎：公开百科检索 → 消歧确认 → GLM 蒸馏风格卡（不搬原台词）
                                      └─ 定时扫券：关键词命中 → 微信图文播报（券图自动压缩）

浏览器 ⇄ FastAPI 网站
          ├─ /        落地页      ├─ /gacha    3D 抽卡屋
          ├─ /onboard 开通向导    └─ /admin    运营管理台

数据库：SQLite（用户 MCP Token 以 Fernet 加密存储，密钥不入库不进仓库）
```

## 🚀 快速开始

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp .env.example .env   # 填入你的 MCP Token 和智谱 API Key
python scripts/bind_wechat.py                        # 微信扫码绑定 ClawBot
python scripts/daemon.py                             # 启动机器人
uvicorn maimai_watchdog.webapp:app --app-dir src --port 7100   # 启动网站
```

详细部署与踩坑记录见 [部署交接.md](部署交接.md) ｜ MCP 接入说明：[MCP_INTEGRATION.md](MCP_INTEGRATION.md)

## 📄 参赛声明与许可

[参赛声明](CONTEST_DECLARATION.md)（官方模板原文，未改动）｜ 代码以 [MIT License](LICENSE) 开源

---

<p align="center">
  <sub>用 🍟 和 ❤️ 做的 · 百变麦小薯 · 觉得好玩就点颗 ⭐ 吧</sub>
</p>
