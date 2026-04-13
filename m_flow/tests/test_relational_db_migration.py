"""
关系数据库迁移测试
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import m_flow
from m_flow.adapters.graph import get_graph_provider
from m_flow.adapters.relational import (
    create_db_and_tables as create_rel_tables,
    get_migration_relational_engine,
)
from m_flow.adapters.vector.pgvector import create_db_and_tables as create_vec_tables
from m_flow.ingestion.pipeline_tasks import migrate_relational_database
from m_flow.search.types import RecallMode


def _nodes_dict(nodes):
    return {n_id: data for (n_id, data) in nodes}


def _normalize_name(name: str) -> str:
    if name and ":" in name:
        p, s = name.split(":", 1)
        return f"{p.capitalize()}:{s}"
    return name


async def _setup_db():
    """设置测试数据库"""
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)
    await create_rel_tables()
    await create_vec_tables()
    return get_migration_relational_engine()


async def _migrate_and_verify():
    """迁移并验证"""
    engine = await _setup_db()
    schema = await engine.extract_schema()
    graph = await get_graph_provider()
    await migrate_relational_database(graph, schema=schema)

    # 搜索验证
    results = await m_flow.search(query_type=RecallMode.TRIPLET_COMPLETION, query_text="Tell me about AC/DC")
    assert any("AC/DC" in r for r in results), "未找到AC/DC"

    db_provider = engine.engine.dialect.name
    rel_label = "reports_to" if db_provider == "postgresql" else "ReportsTo"
    graph_provider = (os.getenv("GRAPH_DATABASE_PROVIDER") or "networkx").lower()

    nodes_set = set()
    edges_set = set()

    if graph_provider == "neo4j":
        rows = await graph.query(f"MATCH (n)-[r:{rel_label}]->(m) RETURN n, r, m")
        for row in rows:
            src = _normalize_name(row["n"].get("name", ""))
            tgt = _normalize_name(row["m"].get("name", ""))
            edges_set.add((src, tgt))
            nodes_set.update([src, tgt])
    elif graph_provider == "kuzu":
        rows = await graph.query(
            f"MATCH (n:Node)-[r:EDGE]->(m:Node) WHERE r.relationship_name = '{rel_label}' RETURN r, n, m"
        )
        for row in rows:
            src = _normalize_name(row[1].get("name", ""))
            tgt = _normalize_name(row[2].get("name", ""))
            if src and tgt:
                edges_set.add((src, tgt))
                nodes_set.update([src, tgt])
    elif graph_provider == "networkx":
        nodes, edges = await graph.get_graph_data()
        nm = _nodes_dict(nodes)
        for s, t, k, _ in edges:
            if k == rel_label:
                src = _normalize_name(nm[s].get("name"))
                tgt = _normalize_name(nm[t].get("name"))
                if src and tgt:
                    edges_set.add((src, tgt))
                    nodes_set.update([src, tgt])

    assert len(nodes_set) == 12, f"期望12个节点，实际{len(nodes_set)}"
    assert len(edges_set) == 15, f"期望15条边，实际{len(edges_set)}"

    expected_edges = {
        ("Employee:5", "Employee:2"),
        ("Employee:2", "Employee:1"),
        ("Employee:4", "Employee:2"),
        ("Employee:6", "Employee:1"),
        ("Employee:8", "Employee:6"),
        ("Employee:7", "Employee:6"),
        ("Employee:3", "Employee:2"),
    }
    for e in expected_edges:
        assert e in edges_set, f"边{e}未找到"

    # 验证总节点和边数
    if db_provider == "sqlite":
        if graph_provider == "networkx":
            nodes, edges = await graph.get_graph_data()
            assert len(nodes) == 543 and len(edges) == 1317
    elif db_provider == "postgresql":
        if graph_provider == "networkx":
            nodes, edges = await graph.get_graph_data()
            assert len(nodes) == 522 and len(edges) == 961

    print(f"迁移验证通过: {graph_provider}")


async def _test_schema_only():
    """仅模式迁移测试"""
    engine = await _setup_db()
    schema = await engine.extract_schema()
    graph = await get_graph_provider()
    await migrate_relational_database(graph, schema=schema, schema_only=True)

    results = await m_flow.search(
        query_text="How many tables",
        query_type=RecallMode.TRIPLET_COMPLETION,
        top_k=30,
    )
    assert any("11" in r for r in results)

    graph_provider = (os.getenv("GRAPH_DATABASE_PROVIDER") or "networkx").lower()
    counts = {"is_part_of": 0, "has_relationship": 0, "foreign_key": 0}

    if graph_provider == "networkx":
        _, edges = await graph.get_graph_data()
        for _, _, k, _ in edges:
            if k in counts:
                counts[k] += 1

        expected = {"is_part_of": 11, "has_relationship": 22, "foreign_key": 11}
        for k, v in expected.items():
            assert counts[k] == v, f"{k}: 期望{v}, 实际{counts[k]}"

    print("模式迁移验证通过")


async def _test_sqlite():
    """SQLite迁移测试"""
    path = os.path.join(pathlib.Path(__file__).parent, "test_data/")
    m_flow.config.set_migration_db_config(
        {
            "migration_db_path": path,
            "migration_db_name": "migration_database.sqlite",
            "migration_db_provider": "sqlite",
        }
    )
    await _migrate_and_verify()
    await _test_schema_only()


async def _test_postgres():
    """PostgreSQL迁移测试"""
    m_flow.config.set_migration_db_config(
        {
            "migration_db_name": "test_migration_db",
            "migration_db_host": "127.0.0.1",
            "migration_db_port": "5432",
            "migration_db_username": "m_flow",
            "migration_db_password": "m_flow",
            "migration_db_provider": "postgres",
        }
    )
    await _migrate_and_verify()
    await _test_schema_only()


async def main():
    print("SQLite迁移测试...")
    await _test_sqlite()
    print("PostgreSQL迁移测试...")
    await _test_postgres()


if __name__ == "__main__":
    asyncio.run(main())
