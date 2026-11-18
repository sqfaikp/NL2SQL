"""
问题改写节点

把用户追问（含指代、省略）结合最近几轮会话历史，补全成语义完整的独立问题。
这是整个图里唯一消费会话历史的节点：改写结果直接覆盖 state["query"]，
下游召回、过滤、SQL 生成的所有节点拿到的都是补全后的问题，逻辑零改动。

设计要点：
1. 无历史时直通放行，不调 LLM，行为与没有记忆功能时完全一致
2. 历史由前端每轮请求携带（最近 3 轮），后端不保存任何会话状态
3. 改写调用失败时回退用原始问题继续问数，绝不因为记忆功能把查询搞挂
"""

from datetime import date

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt

# 每次请求最多携带的历史轮数，与前端截取逻辑保持一致
MAX_HISTORY_ROUNDS = 3


def build_history_text(history: list[dict]) -> str:
    """把历史列表拼成提示词可读的多行文本。"""

    # 第一步：逐轮展开，行号从 1 开始
    history_lines: list[str] = []
    for index, item in enumerate(history, start=1):
        question = item.get("question") or ""
        sql = item.get("sql") or ""

        # 第二步：每轮至少带问题文本，SQL 是上一轮意图的最精确表达，有就带上
        line = f"第{index}轮问题：{question}"
        if sql:
            line += f"；当时生成的SQL：{sql}"

        history_lines.append(line)

    # 第三步：拼成多行文本
    return "\n".join(history_lines)


async def rewrite_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """结合历史把追问补全成独立完整的问题，无历史或失败时原样放行"""

    writer = runtime.stream_writer
    step = "改写问题"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]

        # 第一步：读取前端带来的历史，没有历史就直接放行
        history = state.get("history") or []
        if not history:
            writer({"type": "progress", "step": step, "status": "success"})
            logger.info("无会话历史，跳过问题改写")
            return {"query": query}

        # 第二步：只取最近 MAX_HISTORY_ROUNDS 轮，防止前端误传过长历史
        recent_history = history[-MAX_HISTORY_ROUNDS:]

        # 第三步：把历史拼成提示词文本
        history_text = build_history_text(recent_history)

        # 第四步：准备当前日期，供「上个月」「最近三个月」这类表达换算
        today = date.today()
        current_date = today.strftime("%Y-%m-%d")

        # 第五步：调用 LLM 改写
        prompt = PromptTemplate(
            template=load_prompt("rewrite_query"),
            input_variables=["history_text", "query", "current_date"],
        )
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser
        rewritten = await chain.ainvoke(
            {
                "history_text": history_text,
                "query": query,
                "current_date": current_date,
            }
        )
        rewritten = rewritten.strip()

        # 第六步：改写结果为空时回退原文，不让空文本污染下游
        if not rewritten:
            writer({"type": "progress", "step": step, "status": "success"})
            logger.info("问题改写结果为空，回退原始问题")
            return {"query": query}

        # 第七步：改写成功，用补全后的问题覆盖 state["query"]，
        # 下游所有节点读到的就是独立完整的问题
        writer({"type": "progress", "step": step, "status": "success"})
        logger.info(f"问题改写完成：{query} -> {rewritten}")
        return {"query": rewritten}

    except Exception as error:
        # 第八步：改写失败不能阻断问数，回退原始问题继续走链路，
        # 记忆功能只能锦上添花，绝不能成为新的故障点
        logger.error(f"问题改写失败，回退原始问题继续执行：{error}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"query": state["query"]}
