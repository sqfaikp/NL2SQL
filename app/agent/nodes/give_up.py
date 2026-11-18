"""
SQL 修正放弃节点

连续多次修正仍然无法通过校验时进入这里，不再继续重试，
而是向用户输出一条友好的失败提示，然后正常结束整个流程。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# 修正次数上限。校验失败 -> 修正 -> 重新校验算一轮，
# 连续 MAX_SQL_FIX_ROUNDS 轮都没能通过校验就放弃
MAX_SQL_FIX_ROUNDS = 3


async def give_up(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """连续修正失败后放弃，向用户输出暂不支持的提示"""

    writer = runtime.stream_writer
    step = "校正SQL"

    # 第一步：把步骤条上的“校正SQL”标记为失败状态，前端会显示红色
    writer({"type": "progress", "step": step, "status": "error"})

    # 第二步：输出一条 error 事件。前端收到 error 会把这条消息标记为失败，
    # 并展示这里写的原因，而不是笼统的“查询没有成功”
    message = (
        f"这个问题连续 {MAX_SQL_FIX_ROUNDS} 次 SQL 修正都没能通过校验，暂时无法回答。"
        "可以试试换一种问法，或者把问题拆得更具体一些。"
    )
    writer({"type": "error", "message": message})

    # 第三步：把最后一次校验失败的原因写进日志，方便排查
    # 是召回信息不足、模型反复编造表名，还是其他原因
    logger.error(f"连续修正 SQL 失败，放弃回答。最后一次校验错误：{state['error']}")

    # 第四步：不再返回任何业务数据，流程到此正常结束
    return {}
