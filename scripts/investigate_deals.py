"""调查：中山市麦当劳当前最强优惠明细 + 与门市价对比真实折扣力度。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from maimai_watchdog.mcp_client import McdMcpClient  # noqa: E402

client = McdMcpClient(os.environ["MCD_MCP_TOKEN"])

print("=" * 70)
print("【1】当前全部可领优惠券")
print("=" * 70)
print(client.call_tool("available-coupons", {}))

print()
print("=" * 70)
print("【2】本月活动日历")
print("=" * 70)
print(client.call_tool("campaign-calendar", {}))
