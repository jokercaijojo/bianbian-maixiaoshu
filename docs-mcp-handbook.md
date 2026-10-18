# 麦当劳 MCP 交接手册（全部结论来自 2026-10-11/12 真实调用实测）

> 实测环境：`mcd-probe/` 目录下有完整可运行的调用器 `call2.py`（Python 标准库，无第三方依赖）和每一步的原始响应存档。
> **本文档的每一条参数格式、每一个错误码，都是真调用验证过的，不是从 README 抄的。**

---

## 0. 先回答："为什么别的 MCP 客户端能查到报价，麦麦管家查不到？"

"薯条多少钱"不是一次调用，是**三步串联链**，任何一环断掉都会让 Agent 放弃并回复"看不到报价"：

```
query-nearby-stores（拿 storeCode）
   ↓
query-meals（拿餐品 code + 名称价格）
   ↓
calculate-price（算价，返回 takeWayCode 供下单用）
```

排查优先级（按可能性排序）：

1. **机器人根本没接这三个工具**。它的优惠券功能（轮询 available-coupons、auto-bind-coupons 一键领券、按"薯条/美式/早餐"关键词命中推送）已经跑通，说明 MCP 连接本身没问题——大概率是技能编排里只写了优惠券链路，没写门店/菜单/报价链路。**把本文档第三节的三条链路原样抄进它的技能定义/系统提示词即可。**
2. **踩了参数坑后放弃了**（见第五节排查表）：夜间调用 `query-meals` 不带 `reservationDate` → 返回 600057"门店可能已关闭"；`reservationDate` 格式带秒 → 600052；到店场景误传 `beCode` → 报错。Agent 一次失败后如果没有重试策略，就会对用户说"查不到"。
3. **响应解析失败**：这个 MCP 的响应不是纯 JSON，而是"字段说明 + 原始 JSON"的 Markdown（为 LLM 设计）。如果客户端按纯 JSON 解析会炸。正确做法见第一节。
4. Token 失效（401）——但优惠券推送还在跑，基本可排除。

---

## 1. 连接协议（实测确认）

- **端点**：`POST https://mcp.mcd.cn`，Streamable HTTP，**无会话状态**（initialize 不返回 session id，每个请求独立携带 token 即可，`notifications/initialized` 可发可不发）。
- **请求头**（缺一不可）：
  ```
  Content-Type: application/json
  Accept: application/json, text/event-stream
  Authorization: Bearer <token>
  MCP-Protocol-Version: 2025-06-18
  ```
- **握手**：`initialize` 返回 `{"serverInfo":{"name":"mcd-mcp","version":"1.0.0"},"protocolVersion":"2025-06-18","capabilities":{"tools":{}}}`。
- **标准方法**：`tools/list`（返回 35 个工具）、`tools/call`（`{"name":"...","arguments":{...}}`）。
- **响应形态（重要）**：`tools/call` 的 content 是一段 Markdown——前面是字段说明书，末尾 `## Original Response` 后面跟着**嵌入的原始 JSON**。程序化解析：在文本里找 `{"success"`，从那里 `raw_decode`（注意用 `strict=False`，文本里含字面换行）。`call2.py` 的 `extract_api()` 就是干这个的。
- **限流**：每 token 600 次/分钟，超限 429；token 无效 401。

## 2. 关键工程规则（踩过的坑，直接记）

| 规则 | 说明 |
|---|---|
| **金额单位是"分"** | `calculate-price` 返回 `price: 4000` = ¥40.00。展示必须 ÷100，不然用户看到"薯条 1450 元" |
| **菜单按时段返回** | 同一家店：预约 10:00 只回早餐档 4 个分类；12:00 回全量 16 品。拉菜单必须带对时段的 `reservationDate` |
| **经典产品有全国稳定编码** | 麦香鱼=1600（实测约¥22）、巨无霸=1100（约¥26.5）、薯条=4810、板烧鸡腿堡=1406、麦辣鸡翅=1700、麦乐鸡=1401。**监控/订阅直接按编码匹配**，比按名称可靠 |
| **部分网点是限菜单店** | 会展中心餐厅(1421210)只有16个品（正常门店95~103个），是精选型小店（无堂食、07:30-19:00）。**某产品查不到时先换一家正常门店再试，不要直接结论"该店没卖"** |
| **takeWayList 因店而异** | 正常门店返回 eat-in(堂食)+take-in-store(外带) 两选项；无堂食区的小店只有 take-in-store。create-order 前让用户二选一 |
| **`reservationDate` 格式** | `yyyy-MM-dd HH:mm`，**不带秒**。带秒报 600052 |
| **夜间门店关闭** | 凌晨调 `query-meals`（不带预约时间）→ 600057"门店可能已关闭或不在营业时间"。**正确姿势：改传次日营业时段的 `reservationDate` 重试** |
| **beType 矩阵** | 1=到店自取（**不传 beCode**），2=麦乐送，5=得来速（必须传 beCode），6=企业团餐。`orderType`：1=到店（含DT），2=外送（含团餐） |
| **到店下单必须先报价** | `create-order` 的 `takeWayCode` 只能从 `calculate-price` 返回的 `takeWayList[].code` 里选（如 `take-in-store`）。报价是下单的前置硬依赖 |
| **领券只能全量** | `auto-bind-coupons` 无参数、不能指定某张券。返回 couponId+couponCode 清单 |
| **券的时间校验疑似按"当前时刻"** | "中薯条买一送一"（限周一至五 10:30–23:59）凌晨 00:48 报价时报 600012"不在使用日期内"，改成预约当天 12:00 依然报错。待 10:30 后复测确认 |
| **积分商城列表含过期商品** | 列表里有 `status:2` 但 `downTime` 已过期的商品。**按 upTime/downTime 过滤，不能只看 status** |
| **每手机号=一个账户** | token 即身份；查地址/订单/领券/下单全是账户级操作 |

## 3. 已实测打通的调用链（参数照抄即可用）

### 链路 A：到店报价（机器人缺失的那条）
```
1) query-nearby-stores  {"beType":1,"searchType":2,"city":"深圳市","keyword":"会展中心"}
   → data[].storeCode / storeName / distance / reservationTimeOptions
   ⚠ searchType=1 是搜收藏餐厅，没收藏会报 600050"收藏餐厅列表为空，请您提供城市+关键词"
   ⚠ 用户说"中山的门店"→ city:"中山市" + keyword（如地标/商圈）
2) query-meals  {"storeCode":"1421210","beType":1,"orderType":1,"reservationDate":"2026-10-13 12:00"}
   → data.categories[].meals[].code（分类里只有编码+标签）
   → data.meals = {"4810": {"name":"薯条","currentPrice":"14.5","originalPrice":"14.5",...}, ...}
   ⚠ 报给用户时名称/价格取 data.meals 字典，和分类里的 code 对上
3) calculate-price  {"storeCode":"1421210","orderType":1,"beType":1,
                     "reservationDate":"2026-10-13 12:00",
                     "items":[{"productCode":"9900003586","quantity":1},
                              {"productCode":"4810","quantity":1}]}
   → price:4000（分）= 鳕鱼堡25.5 + 薯条14.5；takeWayList:[{"code":"take-in-store","title":"外带"}]
   ✅ 麦香鱼实测（金田二 1420159）：items=[{"productCode":"1600","quantity":1}] → price 2200（分）= ¥22，
      takeWayList 含 eat-in(堂食)+take-in-store(外带)；detail 同理传 code:"1600"
   ⚠ items 支持 {"couponId","couponCode"} 挂券试价（券不在时段会报 600012，不炸整个请求）
```
餐品特制（去酱/加吉士）：`query-meal-detail` 返回 `rounds[].choices[].modification.items[].selectedKey/unselectedKey`，格式 `{"modification":{"values":[{"code","key","quantity"}]}}`。

### 链路 B：优惠券（机器人已跑通，补充细节）
```
available-coupons {}        → 可领券列表（Markdown 带图片URL，无嵌入JSON）
auto-bind-coupons {}        → 一键领取，实测 7/7 成功，返回 couponId+couponCode
query-my-coupons {"page":"1","pageSize":"50"} → 卡包券+有效期窗口（如 10-12 10:30 ~ 10-16 23:59 限周一至五）
query-store-coupons {"storeCode","beType":1,"orderType":1} → 该店可用的券（会展中心店返回 []，属正常）
```
注意：API 报"领了 7 张"，卡包显示 6 张可用——两处口径不一致（同名券合并/展示规则），**对账别假设数字相等**。

### 链路 C：外送
```
delivery-query-addresses {} → data.addresses[].addressId（真实 addressId 格式：22位长数字串）
delivery-query-stores {"addressId":"...","beType":2} → 可送门店（带 beCode，如 "142083302"，外送下单必须）
```

### 链路 D：积分/抽奖/商城
```
query-my-account {}       → availablePoint "222.7"、currentMouthExpirePoint "20.5"（本月将过期→绝佳提醒素材）
mall-points-products {"catRuleIds":"1>4"} → 积分兑换商品（spuId/point/shopId/status/upTime/downTime）
query-lottery-info {}     → drawPoint "24"、drawDecision.nextConsumption、奖品列表
                            ⚠ 官方工具说明强制要求：展示 nextConsumption 并得到用户明确确认后才能 draw-lottery
query-my-prizes {}        → 抽奖奖品记录（实测为空）
mall-order-list {}        → 商城订单（实测为空）
```

### 链路 E：其他已验证
```
now-time-info {}          → 服务器时间（时区 GMT+8）。机器人判断"现在能不能用券"应以它为准
campaign-calendar {}      → 当月活动日历（Markdown 含图片）
list-nutrition-foods {}   → 160 个餐品营养 CSV 式文本（中薯条 289 千卡等）
order-list {}             → C端历史订单（实测该账户为空，结构含 storeName/orderStatus/orderProductList）
query-survey-coupon {"orderId"} → 需要真实订单号，未测
query-promotions / query-meal-assistance → 团餐场景专用（beType=6），未测
query-party-city/store/date/session / party-order-create → 派对预约，需先拿 spuId，未测
```

## 4. 未执行的写操作（接手前先和用户确认授权）
`create-order`（真下单）、`draw-lottery`（花24积分）、`mall-create-order`（积分兑换）、`cancel-order`、`delivery-create-address`。
官方在工具 description 里写死了确认协议（下单/抽奖必须先向用户展示并确认）——**Agent 自主下单是红线**。

## 5. 错误码速查

| code | 含义 | 处置 |
|---|---|---|
| 401 | token 无效/过期/未提供 | 引导用户重新激活 token |
| 429 | 超 600 次/分钟 | 指数退避重试 |
| 600012 | 券不在使用日期内 | 检查券的有效期窗口（含每日时段） |
| 600050 | 收藏餐厅列表为空 | searchType 改 2 并传 city+keyword |
| 600052 | 预约时间格式错 | 改 `yyyy-MM-dd HH:mm`（无秒） |
| 600057 | 门店可能已关闭/不在营业时间 | 带次日营业时段的 reservationDate 重试 |

## 6. Token 安全（红线）
- token = 用户身份，能查地址/订单、能下单。**日志和错误信息里严禁明文输出 token**；存储加密。
- 本文档写作时用的测试 token 已在聊天中暴露，**建议用户在 open.mcd.cn/mcp 控制台重新激活更换**，旧 token 停用。
