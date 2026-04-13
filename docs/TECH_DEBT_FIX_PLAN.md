# M-flow 图数据库抽象层技术债务修复计划

> **版本**: v2 (2026-04-13)
> **修订说明**: v1 经交叉验证发现 3 个关键错误，已在 v2 中修正。

## 1. 问题总述

### 1.1 根本原因

`GraphProvider` 抽象基类定义了 20 个抽象方法，但存在三层断裂：
1. 业务代码中 **79 处**裸 Cypher 绕过抽象层，95% 使用 Kuzu 专用语法
2. 已有接口方法的返回格式在 Kuzu / Neo4j / Neptune 三端不统一
3. 接口声明的类型签名与实际使用的格式不符（如 `get_edges` 的 `EdgeTuple`）

### 1.2 五个子问题

| # | 子问题 | 影响范围 | 严重度 |
|---|--------|---------|--------|
| A | 裸 Cypher 绑定 Kuzu 方言 | 20+ 文件, 79 处 | 高 |
| B | `query()` 返回格式不统一 | Kuzu=`List[Tuple]`, Neo4j/Neptune=`List[Dict]` | 高 |
| C | 已有接口方法返回格式不一致 | `get_edges`, `get_triplets`, `get_neighbors`, `has_edges` | **极高** |
| D | 接口签名/参数名与实现不符 | `EdgeTuple` 声明 vs 实际返回; `filters` vs `attribute_filters` | 高 |
| E | 缺少 `update_node`、`delete_edge` 等高频操作 | 14 处裸 Cypher 写属性, 3 处裸 Cypher 删边 | 中 |

### 1.3 关键发现（v2 新增）

**`get_edges()` 的真实契约**（经 7 个调用方验证）：

```
实际使用格式: (NodeProps_dict, relationship_name_str, NodeProps_dict)  ← Kuzu 返回（7个调用方期望）
接口声明格式: EdgeTuple = (str, str, str, Dict)                       ← 无人使用此格式
Neo4j 返回:   (src_id_str, dst_id_str, {relationship_name: str})      ← 3-tuple 但内容不兼容
Neptune 返回: (src_id_str, dst_id_str, rel_name_str, props_dict)      ← 4-tuple，匹配 EdgeTuple 但不匹配调用方
```

**修复方向必须是：Neo4j 和 Neptune 都改为匹配 Kuzu 格式（调用方期望的格式），而非反过来。**

---

## 2. 79 处裸 Cypher 分类

按操作语义分为 6 类（详见附录 A）：

| 类别 | 数量 | 替代方案 |
|------|------|---------|
| 按类型列出节点 | 26 | `query_by_attributes([{"type": [...]}])` |
| 子图遍历与聚合 | 20 | `get_edges()` + Python 过滤; 复杂聚合保留 `query()` |
| 更新节点属性 | 14 | 新增 `update_node(node_id, props)` |
| 删除边 | 3 | 新增 `delete_edge(src, dst, rel)` |
| 孤立节点检测 | 8 | `get_graph_data()` + Python 过滤（已在 delete.py 中验证） |
| 健康检查/计数 | 8 | 现有 `is_empty()` / `get_graph_metrics()` |

---

## 3. 修复计划（按阶段）

### 阶段 0: 修正接口文档与类型声明（前置，零风险）

**目标**: 让接口声明反映真实使用情况，消除误导。

**具体动作**:

1. **`graph_db_interface.py`**: 修正 `get_edges` 的返回类型声明和 docstring

```python
# 改前 (与实际不符)
async def get_edges(self, node_id: str) -> List[EdgeTuple]:
    """Get all edges connected to a node."""

# 改后 (反映真实契约)
async def get_edges(self, node_id: str) -> List[Tuple[NodeProps, str, NodeProps]]:
    """Get all edges connected to a node.

    Returns list of (source_node_props, relationship_name, target_node_props).
    Both source and target are property dicts with at least 'id', 'type', 'name' keys.
    """
```

2. **修正 `query_by_attributes` 参数名**:
   - 接口声明 `filters` → 改为 `attribute_filters`（匹配三个实现和所有调用方）

3. **修正 `has_edges` 返回类型文档**: 明确 Neptune 返回 `List[bool]` 的行为是否为预期

### 阶段 1: 统一 `get_edges` / `get_triplets` / `get_neighbors` 返回格式

**目标**: Neo4j 和 Neptune 改为返回与 Kuzu 相同的格式（所有调用方期望的格式）。

**`get_edges` 统一为 `(NodeProps, str, NodeProps)`**:

| 适配器 | 现状 | 改动 |
|--------|------|------|
| Kuzu | `(src_dict, rel_name, dst_dict)` | **不动**（基准格式，7 个调用方依赖） |
| Neo4j | `(src_id, dst_id, {rel_name})` — 3-tuple 但内容不兼容 | 改为返回完整节点 dict + 关系名字符串 |
| Neptune | `(src_id, dst_id, rel_name, props)` — 4-tuple | 改为 3-tuple：返回完整节点 dict + 关系名字符串 |

**Neo4j `get_edges` 改法**:

```python
# 改前
async def get_edges(self, node_id: str):
    cypher = f"MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})-[r]-(m) RETURN n, r, m"
    results = await self.query(cypher, {"nid": node_id})
    return [(r["n"]["id"], r["m"]["id"], {"relationship_name": r["r"][1]}) for r in results]

# 改后
async def get_edges(self, node_id: str):
    cypher = f"""
    MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})-[r]-(m)
    RETURN properties(n) AS src, TYPE(r) AS rel, properties(m) AS dst
    """
    results = await self.query(cypher, {"nid": node_id})
    return [(r["src"], r["rel"], r["dst"]) for r in results]
```

**`get_triplets` 统一为 `(NodeProps, edge_dict, NodeProps)`**:

| 适配器 | 现状 | 改动 |
|--------|------|------|
| Kuzu | `(src_dict, edge_dict, dst_dict)` | **不动** |
| Neo4j | `(r[0], {"relationship_name": r[1]}, r[2])` | 改为返回完整属性 dict |
| Neptune | 已匹配 Kuzu 格式 | **不动** |

**`get_neighbors` 修复 Neo4j 缺失实现**:

```python
# Neo4j 现状: get_neighbors 调用 self.get_neighbours() → AttributeError
# 修复: 实现 get_neighbors 直接查询
async def get_neighbors(self, node_id: str) -> List[Dict[str, Any]]:
    cypher = f"""
    MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})-[r]-(m)
    RETURN DISTINCT properties(m) AS props
    """
    results = await self.query(cypher, {"nid": node_id})
    return [r["props"] for r in results]
```

**安全措施**:
- Kuzu 格式不动 → 7 个生产调用方零影响
- Neo4j 改动只影响 Neo4j 部署（当前 CI 只测 Kuzu）
- Neptune 需验证 `_transform_record_to_edge` 输出格式

### 阶段 2: 扩展 GraphProvider 接口（仅必要新增）

**原计划 5 个新增方法 → 修正为 3 个**:

| 方法 | 理由 |
|------|------|
| ~~`list_nodes_by_type`~~ | **取消**: `query_by_attributes([{"type": [...]}])` 已能实现 |
| `update_node(node_id: str, props: Dict) -> None` | **保留**: `add_node` 整体替换 properties JSON，业务需要 **合并（merge）** 语义——读取现有属性、合并新键值、写回。Kuzu 存储为 JSON STRING 列需 RMW，Neo4j 存储为顶层属性可直接 `SET n += $props` |
| `delete_edge(src: str, dst: str, rel: str) -> None` | **保留**: 3 处裸 Cypher 删边无等价接口 |
| `get_document_subgraph(data_id: str) -> Optional[Dict]` | **保留**: 已在三个适配器中实现，只需提升到接口 |
| ~~`get_degree_one_nodes`~~ | **取消**: 只有 hard delete 用到，可用 `get_graph_data` + Python 过滤替代 |

**新增发现需修复**:

| 修复项 | 说明 |
|--------|------|
| `query_by_attributes` 参数名统一 | 接口 `filters` → `attribute_filters`（或反向统一） |
| Neo4j `query_by_attributes` 注入风险 | 字符串拼接 → 参数化查询 |
| Neptune `has_edges` 返回类型 | `List[bool]` → 应与接口约定一致 |
| Neptune 工厂 import 错误 | `NEPTUNE_ENDPOINT_URL` 常量不存在 |

### 阶段 3: 业务代码迁移（按批次）

**迁移策略修正**: 不新增 `list_nodes_by_type`，改用现有 `query_by_attributes`。

| 批次 | 文件 | 数量 | 替代方式 |
|------|------|------|---------|
| 3a | `node_deletion.py` | 4 | `get_graph_data()` + Python（与 delete.py 一致） |
| 3b | `learn.py` | 1 | `query_by_attributes([{"type": ["Episode"]}])` → 取 nodes |
| 3c | `get_prune_procedural_router.py` | 5 | `query_by_attributes` + `delete_nodes` + `update_node` |
| 3d | `entity_lookup.py` | 2 | `query_by_attributes([{"type": ["Entity"]}])` |
| 3e | `procedure_state.py` | 1 | `query_by_attributes` |
| 3f | `write_procedural_from_episodic.py` | 4 | `query_by_attributes` + `get_node` + `update_node` |
| 3g | `procedure_router.py`, `procedure_builder/write.py` | 4 | `get_node` + `update_node` |
| 3h | `reconcile_active.py`, `update_usage_stats.py` | 5 | `query_by_attributes` + `update_node` |
| 3i | `episode_size_check.py` | 7 | 混合: 简单查询用 `get_edges`, 聚合保留 `query()` |
| 3j | `get_graph_router.py` | 5 | 混合: 子图用 `get_triplets`, 聚合保留 `query()` |
| 3k | `entity_description_optimizer.py` | 5 | `get_edges` + `update_node`（其中 1 处边属性更新 `SET r.edge_text` 需保留 `query()`，无抽象接口可替代） |
| 3l | `migrate_aliases_to_facet_points.py` | 3 | `query_by_attributes` + `get_edges` |
| 3m | `health.py`, `scripts/*`, `examples/*` | 26 | `is_empty()` / 保留（非生产路径） |

### 阶段 4: `node_deletion.py` 改为 Provider 无关

与 `delete.py` 已完成的 Phase 2/3 模式一致：

```python
# 用 get_graph_data() + Python 过滤替代裸 Cypher 孤立节点检测
nodes, edges = await graph.get_graph_data()
connected = {src for src, _, _, _ in edges} | {tgt for _, tgt, _, _ in edges}
# 注意: get_graph_data 返回 4-tuple (src, tgt, rel, props)
# 而 get_edges 返回 3-tuple (src_dict, rel, dst_dict) — 格式不同！
for nid, props in nodes:
    if props.get("type") == "Facet" and nid not in connected:
        ...
```

### 阶段 5: 集成测试

新增测试覆盖：
1. `get_edges()` 返回格式一致性（跨后端）
2. `get_triplets()` 返回格式一致性
3. `node_deletion.py` 级联删除
4. `update_node()` 在各后端行为一致

---

## 4. 不应修改的内容

| 内容 | 原因 |
|------|------|
| Kuzu `get_edges()` 返回格式 | 7 个生产调用方依赖此格式，是事实标准 |
| `CypherSearchRetriever` | 设计意图即为用户传入任意 Cypher |
| `test_remote_kuzu_stress.py` | Kuzu DDL 压力测试专用 |
| `test_relational_db_migration.py` 分支 Cypher | 已按 provider 分支，各自正确 |
| `episode_size_check.py` 中的复杂聚合 Cypher | 无法用现有接口替代，保留 `query()` |
| `get_graph_router.py` 中的 overview 聚合查询 | 同上 |

---

## 5. 关键风险与缓解

### 风险 1: 修改 `get_edges` 返回格式导致回归
- **缓解**: 不动 Kuzu（基准），只改 Neo4j/Neptune 以匹配
- **验证**: CI 默认用 Kuzu，Neo4j/Neptune 改动不影响现有 CI

### 风险 2: `query_by_attributes` 替代裸 Cypher 时行为不一致
- **发现**: Neo4j 只用 `filters[0]`，Kuzu 用所有 filters
- **缓解**: 业务代码只传单个 filter dict（与两端行为一致的子集）
- **长期修复**: 统一 Neo4j 行为（阶段 2 一并处理）

### 风险 3: Neptune 适配器潜在问题
- **发现 1**: `NEPTUNE_ENDPOINT_URL` 导入不存在 → 工厂崩溃
- **发现 2**: `has_edges` 返回 `List[bool]` 而非 `List[EdgeTuple]`
- **缓解**: 阶段 1 中一并修复

### 风险 4: Kuzu 点路径 JSON 语法不可移植
- **发现**: `reconcile_active.py` 和 `update_usage_stats.py` 使用 `SET p.properties.status = 'value'`（Kuzu JSON 扩展的点路径语法）
- **问题**: `properties` 在 Kuzu 中是 STRING 列（存 JSON），点路径依赖 Kuzu JSON 扩展；Neo4j 中 `properties` 是一个普通属性名，`.status` 语法无效
- **缓解**: 阶段 3h 中将这些改为 `update_node()` 的合并语义（各适配器内部处理差异）

### 风险 5: `get_graph_data` 与 `get_edges` 格式不同易混淆
- `get_graph_data` 边: `(src_id, tgt_id, rel_name, props_dict)` — 4-tuple, ID 为字符串
- `get_edges` 边: `(src_dict, rel_name, dst_dict)` — 3-tuple, 节点为完整 dict
- **缓解**: 在接口文档中明确标注两者差异

---

## 6. 实施时间线

| 阶段 | 工作量 | 风险 | 依赖 |
|------|-------|------|------|
| 阶段 0（接口文档修正） | 0.5h | 无 | 无 |
| 阶段 1（返回格式统一） | 3-4h | 中（只改 Neo4j/Neptune） | 阶段 0 |
| 阶段 2（接口扩展 + 不一致修复） | 3-4h | 中 | 阶段 0 |
| 阶段 3a-3h（核心迁移） | 4-6h | 中 | 阶段 2 |
| 阶段 3i-3m（复杂/非核心迁移） | 3-4h | 低 | 阶段 2 |
| 阶段 4（node_deletion） | 1-2h | 低 | 阶段 1 |
| 阶段 5（集成测试） | 3-4h | 无 | 贯穿 |

---

## 附录 A: 79 处裸 Cypher 完整清单

### A.1 按类型列出节点 (26 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `learn.py` | 171-176 | `MATCH (n:Node) WHERE n.type = 'Episode' RETURN ...` |
| `procedure_state.py` | 379-384 | `MATCH (n:Node) WHERE n.type = 'Procedure' RETURN ...` |
| `write_procedural_from_episodic.py` | 224-228 | `MATCH (e:Node) WHERE e.type = 'Episode' AND e.id IN $ids RETURN ...` |
| `write_procedural_from_episodic.py` | 233-237 | `MATCH (e:Node) WHERE e.type = 'Episode' RETURN ...` |
| `entity_lookup.py` | 49-55, 125-131 | `MATCH (n:Node) WHERE n.type IN ['Entity'] RETURN ...` |
| `migrate_aliases_to_facet_points.py` | 118-124 | `MATCH (n:Node) WHERE n.type = "Facet" RETURN ...` |
| `migrate_aliases_to_facet_points.py` | 216-222 | `MATCH (n:Node) WHERE n.type = "MemorySpace" RETURN ...` |
| `episode_size_check.py` | 732-737 | `MATCH (ep:Node {type: "Episode", id: $id}) RETURN ep` |
| `get_prune_procedural_router.py` | 91-92 | `MATCH (n:Node) WHERE n.type IN [...] RETURN count(*)` |
| `get_prune_procedural_router.py` | 108-109 | `MATCH (e:Node) WHERE e.type = 'Episode' RETURN ...` |
| `migrate_created_at.py` | 54-57, 129-132, 208-211, 287-290, 366-370, 443 | 多类型节点列出 |
| `migrate_lancedb_created_at.py` | 47-52 | Episode 列出 |
| 6 个 examples 文件 | 各处 | Episode/Facet/Entity 计数 |

### A.2 子图遍历与聚合 (20 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `get_graph_router.py` | 266-278 | Episode 子图 OPTIONAL MATCH |
| `get_graph_router.py` | 419-431 | Facet 子图 |
| `get_graph_router.py` | 572-581 | Episodes overview 聚合 |
| `get_graph_router.py` | 855-862 | Procedures overview |
| `get_graph_router.py` | 934-941 | Procedure 子图 |
| `episode_size_check.py` | 284-295, 716-723, 896-903, 990-997 | Facet 聚合, 排序, ContentFragment/Entity 查找 |
| `entity_description_optimizer.py` | 161-165, 169-173, 258-262 | Entity 边遍历 |
| `migrate_aliases_to_facet_points.py` | 144-150 | Facet → FacetPoint 边 |
| `migrate_created_at.py` | 129-132, 208-211, 287-290 | 跨节点时间戳传播 |

### A.3 更新节点属性 (14 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `write_procedural_from_episodic.py` | 378-382, 401-407 | `SET n.properties = $props` |
| `procedure_router.py` | 428-432, 468-477 | 同上 |
| `procedure_builder/write.py` | 255-259, 281-285 | 同上 |
| `reconcile_active.py` | 92-96, 105-109 | `SET p.properties.status = 'superseded'` |
| `update_usage_stats.py` | 86-92, 114-121 | `SET p.properties.used_count = ...` |
| `get_prune_procedural_router.py` | 119-122 | `SET n.properties = $props` |
| `entity_description_optimizer.py` | 243-247 | `SET c.description = $desc` |
| `migrate_created_at.py` | 100-105, 178-183 | `SET n.created_at = timestamp()` |

### A.4 删除边 (3 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `episode_size_check.py` | 822-827 | `DELETE r` (has_facet 边) |
| `get_prune_procedural_router.py` | 98-101 | `DELETE r` (Procedure 相关边) |
| `get_prune_procedural_router.py` | 103-104 | `DELETE n` (Procedure 节点) |

### A.5 孤立节点检测 (8 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `node_deletion.py` | 186-192 | `COUNT { MATCH (f)--() } as degree WHERE degree = 0` |
| `node_deletion.py` | 209-215 | FacetPoint 同上 |
| `node_deletion.py` | 224-230 | Entity 同上 |
| `node_deletion.py` | 242-248 | Facet 分支同上 |
| `delete.py` | — | **已修复** (用 get_graph_data + Python) |

### A.6 健康检查 / 计数 (8 处)

| 文件 | 行号 | Cypher 模式 |
|------|------|------------|
| `health.py` | 152 | `MATCH () RETURN count(*) LIMIT 1` |
| `subprocesses/reader.py` | 21 | `MATCH (n:Node) RETURN COUNT(n)` |
| `migrate_created_at.py` | 443 | `MATCH (n:Node) RETURN count(n)` |
| examples 5 处 | 各处 | 各类型 count |
