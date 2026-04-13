# 删除级联修复计划 — 覆盖情景记忆层

## 问题

当前 `delete()` 只删除摄入层（Document → ContentFragment → FragmentDigest → 孤立 Entity），完全遗漏了 `memorize()` 创建的情景记忆层（Episode → Facet → FacetPoint）。删除后图中残留大量"幽灵节点"。

## 完整图结构

```
TextDocument                                          Episode
    ↑ is_part_of                                     ↗      ↘
ContentFragment ──contains──→ Entity ──is_a──→ EntityType  has_facet
    ↑ made_from       ↑                                      ↓
FragmentDigest    involves_entity                           Facet
                  (from Episode)                         ↙       ↘
                  (from Facet)                supported_by   has_point
                                           (→ContentFragment)    ↓
                                                           FacetPoint
Episode ──includes_chunk──→ ContentFragment
```

## 当前删除 vs 应该删除

| 节点类型 | 当前 | 应该 |
|---------|:---:|:---:|
| TextDocument | ✅ | ✅ |
| ContentFragment | ✅ | ✅ |
| FragmentDigest | ✅ | ✅ |
| Entity (孤立) | ✅ | ✅ |
| EntityType (孤立) | ✅ | ✅ |
| **Episode** | ❌ | ✅ (当所有 source chunks 都被删时) |
| **Facet** | ❌ | ✅ (当 supported_by 的 chunks 都被删时) |
| **FacetPoint** | ❌ | ✅ (随 Facet 一起删) |
| **向量存储** (Episode_summary 等) | ❌ | ✅ |

## 核心难点：Episode 共享

Episode 可以通过 episode routing 聚合多个文档的内容。删除单个文档时：
- 如果 Episode 只关联被删文档 → 安全删除整个 Episode
- 如果 Episode 还关联其他文档 → 只删除来自该文档的 Facet，保留 Episode

判断依据：`Facet --supported_by--> ContentFragment --is_part_of--> TextDocument`

## 修复方案：两阶段删除（推荐）

经过深入验证，**两阶段方案比单次复杂查询更安全**：

### 为什么选择两阶段

单次 Cypher 查询方案需要复杂的 `NOT EXISTS` 多跳子查询来判断 Episode 共享——这在 KuzuDB 中语法支持不确定，且容易出错。

两阶段方案利用了 `DETACH DELETE` 的特性：
1. 删除 ContentFragment 时，`DETACH DELETE` 自动清除 `includes_chunk` 和 `supported_by` 边
2. 边清除后，孤立的 Episode/Facet 自然浮现——无需复杂查询判断

### 阶段 1：现有删除（不变）

```python
# 现有 _remove_subgraph 逻辑
# 删除: Document, ContentFragment, FragmentDigest, 孤立 Entity/EntityType
# DETACH DELETE 自动移除了指向这些节点的所有边
```

### 阶段 2：清理孤立的情景记忆节点（新增）

在阶段 1 完成后，追加一轮清理：

```python
async def _cleanup_orphan_episodic_nodes(graph) -> list[str]:
    """删除没有任何证据链的 Episode/Facet/FacetPoint 节点。
    
    阶段 1 删除了 ContentFragment 并通过 DETACH DELETE 自动移除了：
    - Episode → ContentFragment 的 includes_chunk 边
    - Facet → ContentFragment 的 supported_by 边
    
    此时如果一个 Episode 没有任何 includes_chunk 边剩余，
    说明它的所有证据来源都已被删除——安全删除。
    """
    purged = []
    
    # 1. 找到没有 supported_by 边的 Facet → 删除其 FacetPoint
    orphan_facets = await graph.query("""
        MATCH (f:Node) WHERE f.type = 'Facet'
        AND NOT EXISTS { MATCH (f)-[r:EDGE]->() WHERE r.relationship_name = 'supported_by' }
        RETURN f
    """)
    for facet in orphan_facets:
        # 先删 FacetPoint
        fps = await graph.query("""
            MATCH (f:Node)-[r:EDGE]->(fp:Node) 
            WHERE f.id = $id AND r.relationship_name = 'has_point'
            RETURN fp
        """, {"id": facet["id"]})
        for fp in fps:
            await graph.delete_node(fp["id"])
            purged.append(fp["id"])
        # 再删 Facet
        await graph.delete_node(facet["id"])
        purged.append(facet["id"])
    
    # 2. 找到没有 includes_chunk 边的 Episode → 删除
    orphan_episodes = await graph.query("""
        MATCH (e:Node) WHERE e.type = 'Episode'
        AND NOT EXISTS { MATCH (e)-[r:EDGE]->() WHERE r.relationship_name = 'includes_chunk' }
        RETURN e
    """)
    for ep in orphan_episodes:
        await graph.delete_node(ep["id"])  # DETACH 自动清 has_facet/involves_entity 残留边
        purged.append(ep["id"])
    
    return purged
```

### 集成方式

在 `delete_single_document` 中，阶段 1 完成后调用阶段 2：

```python
async def delete_single_document(data_id, dataset_id, mode):
    # 阶段 1: 现有逻辑（删除 Document/Chunk/Digest/Entity）
    graph_result = await _remove_subgraph(data_id, mode)
    uuid_list = [...]
    
    # 阶段 2: 清理孤立的情景记忆节点（新增）
    graph = await get_graph_provider()
    orphan_ids = await _cleanup_orphan_episodic_nodes(graph)
    uuid_list.extend([_convert_to_uuid(oid) for oid in orphan_ids if _convert_to_uuid(oid)])
    
    # 向量存储清理（自动覆盖 Episode/Facet/FacetPoint）
    await _purge_from_vector_store(uuid_list)
    # 关系数据库清理
    await _purge_from_relational(data_id, dataset_id, uuid_list)
```

### 向量存储清理

**不需要改 `_purge_from_vector_store`**！验证发现：
- `_discover_vector_collections()` 动态发现所有集合（包括 Episode_summary、Facet_search_text 等）
- `delete_memory_nodes()` 按 node ID 从所有集合中删除
- 只要 Episode/Facet/FacetPoint 的 ID 进入 `uuid_list`，向量自动清理

### 涉及的文件

| 文件 | 修改内容 | 复杂度 |
|------|---------|--------|
| `m_flow/api/v1/delete/delete.py` | 新增 `_cleanup_orphan_episodic_nodes`，在 `delete_single_document` 中调用 | 中 |

**只需要改 1 个文件**。图适配器不需要改——使用通用的 `query()` 和 `delete_node()` 接口。

### 安全保证

1. **不会误删共享 Episode** — 阶段 2 只删除"没有任何 includes_chunk 边"的 Episode。如果其他文档的 chunks 还连着，边仍在，Episode 不会被选中
2. **不会误删共享 Facet** — 同理，只删除"没有任何 supported_by 边"的 Facet
3. **两阶段天然正确** — 阶段 1 的 DETACH DELETE 精确移除了被删文档的边，阶段 2 只清理"失去所有证据"的节点
4. **不依赖复杂 Cypher** — 只用 `NOT EXISTS { MATCH (n)-[r]->() WHERE ... }` 单跳查询，所有图引擎都支持
5. **向量自动清理** — ID 进入 uuid_list 即自动从所有集合删除

### 测试验证

```
测试流程：add 3 files → memorize → delete each → assert graph empty

阶段 1 后: Document/ContentFragment/FragmentDigest 已删，边也自动清
阶段 2 后: 孤立的 Episode/Facet/FacetPoint 被清理
结果: 图中节点和边都为 0 → 断言通过
```

### 风险评估

| 风险 | 概率 | 缓解 |
|------|------|------|
| `NOT EXISTS { MATCH (n)-[r]->() }` KuzuDB 兼容性 | 低 | 已有类似用法在 `get_document_subgraph` 中 |
| 阶段 2 误删全局孤立节点（非当前删除操作产生的） | 中 | 可以加时间戳过滤，只清理"最近变成孤立的"；或接受全局清理作为正向效果 |
| 并发删除操作竞态 | 低 | 当前系统无并发删除设计，单用户操作 |
