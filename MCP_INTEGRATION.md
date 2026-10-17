# MCP 接入说明

本项目基于**麦当劳中国官方 MCP Server** 构建，以下说明实际使用的 Server、Tool、
调用流程与业务价值。

## MCP Server

| 项 | 值 |
|---|---|
| Endpoint | `https://mcp.mcd.cn` |
| 协议 | Streamable HTTP（JSON-RPC 2.0，SSE 响应解析） |
| 认证 | `Authorization: Bearer <MCP_TOKEN>`（Token 于 open.mcd.cn 申请，环境变量注入） |
| 客户端实现 | `src/maimai_watchdog/mcp_client.py`（纯标准库，解析 SSE `data:` 帧） |

## 实际使用的 Tool 清单

| Tool | 用途 | 调用方 |
|---|---|---|
| `available-coupons` | 拉取当前全部可领取优惠券（含官方配图 URL） | 定时蹲点扫描 / 微信「查券」指令 |
| `campaign-calendar` | 拉取当月营销活动日历（含活动配图 URL） | 定时蹲点扫描 |
| `auto-bind-coupons` | 一键领取全部可领券到用户卡包（写操作） | 微信「领券」指令（用户明确触发才执行） |
| `query-nearby-stores` | 按城市+地标查门店，返回 storeCode | GLM 代理（查价链路第一步） |
| `query-meals` | 查门店可售菜单（餐品编码、现价、划线价） | GLM 代理（查价链路第二步） |
| `calculate-price` | 计算选购商品总价 | GLM 代理（查价链路第三步） |
| `query-my-coupons` | 查用户卡包已有券 | GLM 代理（"我有什么券"） |
| `query-my-account` | 查用户积分账户 | GLM 代理（"我多少积分"） |
| `now-time-info` | 服务器时间探活 | 网站注册时校验用户 Token 真伪 |

## 核心调用流程

### 流程一：优惠蹲点（每 30 分钟自动执行）

```
daemon 调度
  → available-coupons        （券池：标题 + 配图 URL）
  → campaign-calendar        （活动池：标题 + 配图 URL）
  → 关键词匹配（用户自定义关注清单，如"麦香鱼"）
  → SQLite 去重（同一条目只报一次）
  → 微信图文播报：报喜文案 + 官方配图（iLink 图片消息）
```

业务价值：把"人刷 App 找券"变成"券找人"。实测官方券力度可达
单品 5 折（中薯条买一送一）甚至免费（脆薯饼），但限量限时段，
第一时间触达就是真实省钱。

### 流程二：智能查价（GLM Function Calling 自主编排）

```
用户："麦香鱼多少钱？我在中山轻轨站附近"
  → GLM 决策调用 query-nearby-stores(city=中山市, keyword=轻轨站)
  → 拿到 storeCode 后自主调用 query-meals 查菜单
  → 自主调用 calculate-price 算价
  → 生成人话回答（金额单位"分"→"元"的换算由系统提示词约束）
```

业务价值：用户不用打开 App、不用知道门店编码，一句口语即可获得
**实时真实价格**；多轮工具链路由 LLM 自主完成，无需硬编码编排。

### 流程三：一键领券（写操作，有红线）

```
用户："领券"
  → auto-bind-coupons（全量领取）
  → 回报领取结果
```

红线：所有写操作必须由用户明确指令触发，机器人永不自动领券。

## 接入中踩过的坑（供其他参赛者参考）

1. **金额单位是"分"**：`price:4000` = ¥40.00，展示前必须 ÷100
2. **`reservationDate` 格式**：`yyyy-MM-dd HH:mm`，**不带秒**；带秒会报错
3. **夜间查菜单**：门店关闭时报错 `600057`，改用次日 12:00 的预约时间重试即可
4. **SSE 响应**：`query-meals` 返回体较大，客户端需按 `data:` 行流式拼接
5. **`auto-bind-coupons` 只能全量领**：不支持指定单张券，产品设计时需向用户说明
