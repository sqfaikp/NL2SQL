"""
元数据库 MySQL 仓储

这一层对应文档里的 Meta Repository，负责接收业务实体并落到 Meta MySQL
Repository 自身只关心“如何写入”，而“哪些写操作要放在同一笔事务里”，由 Service 层统一决定

表 字段 指标和字段指标关系都会先以业务实体流转，再在这里统一转成 ORM 模型
问数链路运行时也会从这里读取元数据，用来把召回到的 id 补齐成完整实体
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.table_info import TableInfo
from app.models.column_info import ColumnInfoMySQL
from app.models.table_info import TableInfoMySQL
from app.repositories.mysql.meta.mappers.column_info_mapper import ColumnInfoMapper
from app.repositories.mysql.meta.mappers.column_metric_mapper import ColumnMetricMapper
from app.repositories.mysql.meta.mappers.metric_info_mapper import MetricInfoMapper
from app.repositories.mysql.meta.mappers.table_info_mapper import TableInfoMapper


class MetaMySQLRepository:
    """负责把元数据业务实体持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_table_infos(self, table_infos: list[TableInfo]):
        """保存表元数据：按主键覆盖，保证同一份配置反复构建的结果稳定。

        这里用 merge 而不是 add_all，是因为表元数据的主键就是表名。
        第二次构建同一份 meta_config.yaml 时，add_all 会把它当成新记录插进去，
        直接撞主键唯一约束报 IntegrityError；而 merge 的语义是
        “主键已存在就更新，不存在才插入”，天然幂等。
        """

        # 第一步：逐张表处理，不再一次性 add_all
        for table_info in table_infos:

            # 第二步：业务实体转 ORM 模型，转换细节统一交给 Mapper
            table_model = TableInfoMapper.to_model(table_info)

            # 第三步：merge 会先按主键查一次，已存在就更新，不存在才插入
            await self.session.merge(table_model)

    async def save_column_infos(self, column_infos: list[ColumnInfo]):
        """保存字段元数据：按主键覆盖，字段 id 形如 表名.字段名，可重复写入。"""

        # 第一步：逐个字段处理
        for column_info in column_infos:

            # 第二步：实体转 ORM 模型
            column_model = ColumnInfoMapper.to_model(column_info)

            # 第三步：交给 merge 按主键覆盖
            await self.session.merge(column_model)

    async def save_metric_infos(self, metric_infos: list[MetricInfo]):
        """保存指标元数据：按主键覆盖，指标 id 直接用指标名。"""

        # 第一步：逐个指标处理
        for metric_info in metric_infos:

            # 第二步：实体转 ORM 模型
            metric_model = MetricInfoMapper.to_model(metric_info)

            # 第三步：交给 merge 按主键覆盖
            await self.session.merge(metric_model)

    async def save_column_metrics(self, column_metrics: list[ColumnMetric]):
        """保存字段与指标的关联关系：复合主键是 (column_id, metric_id)，同样可重复写入。"""

        # 第一步：逐条关联关系处理
        for column_metric in column_metrics:

            # 第二步：实体转 ORM 模型，复合主键两个字段都来自实体本身
            column_metric_model = ColumnMetricMapper.to_model(column_metric)

            # 第三步：交给 merge 按复合主键覆盖
            await self.session.merge(column_metric_model)

    async def get_column_info_by_id(self, id: str) -> ColumnInfo | None:
        """按字段 id 查询字段元数据，供召回信息合并阶段补齐字段上下文"""

        column_info: ColumnInfoMySQL | None = await self.session.get(
            ColumnInfoMySQL, id
        )
        if column_info:
            return ColumnInfoMapper.to_entity(column_info)
        else:
            return None

    async def get_table_info_by_id(self, id: str) -> TableInfo | None:
        """按表 id 查询表元数据，最终组装成提示词里的表结构信息"""

        table_info: TableInfoMySQL | None = await self.session.get(TableInfoMySQL, id)
        if table_info:
            return TableInfoMapper.to_entity(table_info)
        else:
            return None

    async def get_key_columns_by_table_id(self, table_id: str) -> list[ColumnInfo]:
        """查询指定表的主外键字段，避免 Join 关键字段被向量召回漏掉"""

        # 主外键字段用于后续生成 join 条件，不能完全依赖向量召回命中
        # 这里走 ORM 查询而不是裸 SQL：column_info 的 examples / alias 是 JSON 列，
        # 只有带上 ORM 列类型信息，SQLAlchemy 才会把 JSON 字符串反序列化成 list，
        # 和 get_column_info_by_id() 保持一致的返回值形态
        stmt = select(ColumnInfoMySQL).where(
            ColumnInfoMySQL.table_id == table_id,
            ColumnInfoMySQL.role.in_(("primary_key", "foreign_key")),
        )
        result = await self.session.scalars(stmt)
        return [ColumnInfoMapper.to_entity(model) for model in result.all()]
