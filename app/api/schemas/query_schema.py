"""
问数接口请求体定义

集中声明 API 层输入输出的数据结构，让路由函数只处理业务流程，
字段校验和 OpenAPI 文档生成交给 Pydantic 与 FastAPI 完成。
"""

from pydantic import BaseModel, Field


class HistoryItem(BaseModel):
    """一轮历史对话：用户问了什么，系统最终用了哪条 SQL"""

    # 该轮的用户原始问题
    question: str
    # 该轮最终执行的 SQL。修正失败或未走到生成 SQL 的轮次可以为空
    sql: str | None = None


class QuerySchema(BaseModel):
    """`/api/query` 请求体，承载用户输入的自然语言问题和会话历史"""

    # 前端请求体中的 query 字段，例如 {"query": "统计华北地区销售额"}
    query: str
    # 最近几轮会话历史，用于把「那上个月呢」这类追问补全成独立问题。
    # 后端不保存会话，历史完全由前端维护并每轮携带
    history: list[HistoryItem] = Field(default_factory=list)
