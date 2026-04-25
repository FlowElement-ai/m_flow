# M-flow MCP Server

> Model Context Protocol server for M-flow knowledge graph

M-flow MCP Server 将 M-flow 知识图谱功能暴露给 AI 助手（如 Cursor、Claude Desktop、VS Code + Continue），使其能够直接与你的知识库交互。

## 快速开始

### Docker 部署（推荐）

```bash
docker compose --profile mcp up -d
```

MCP 服务运行在 `http://localhost:8001`

### 本地运行

```bash
cd m_flow-mcp
pip install -e .
python src/server.py --transport sse --port 8000
```

## 可用工具

| 工具 | 说明 | 核心参数 |
|------|------|----------|
| `memorize` | 将数据转化为知识图谱 | `data` (必需), `dataset_name`, `wait` |
| `save_interaction` | 保存用户-Agent 交互 | `data` (必需), `wait` |
| `search` | 搜索知识图谱 | `search_query`, `recall_mode`, `top_k`, `datasets`, `system_prompt`, `enable_hybrid_search` |
| `list_data` | 列出数据集 | `dataset_id` (可选) |
| `delete` | 删除数据 | `data_id`, `dataset_id`, `mode` |
| `prune` | 选择性清空知识图谱 | `graph`, `vector`, `metadata`, `cache` |
| `memorize_status` | 查询处理状态 | `task_id` (可选) |
| `learn` | 提取程序性记忆 | `datasets`, `episode_ids`, `run_in_background` |
| `update_data` | 更新已有数据 | `data_id` (必需), `data` (必需), `dataset_id` (必需) |
| `ingest` | 一步入库 | `data` (必需), `dataset_name`, `skip_memorize` |
| `query` | 简化查询 | `question` (必需), `datasets`, `mode`, `top_k` |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TRANSPORT_MODE` | 传输模式 (stdio/sse/http) | Docker: `sse`, 本地: `stdio` |
| `MCP_PORT` | 服务端口 | `8000` |
| `MCP_LOG_LEVEL` | 日志级别 | `info` |

## 传输模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `stdio` | 标准输入/输出 | IDE 集成（Cursor、Claude Desktop） |
| `sse` | Server-Sent Events | HTTP 长连接、Web 客户端 |
| `http` | 标准 HTTP | REST API 调用 |

## 高级配置

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--transport` | 传输模式 | `stdio` |
| `--host` | 绑定主机 | `127.0.0.1` |
| `--port` | 绑定端口 | `8000` |
| `--log-level` | 日志级别 | `info` |
| `--api-url` | M-flow API URL（启用 API 模式） | 无 |
| `--api-token` | API 认证令牌 | 无 |
| `--no-migration` | 跳过数据库迁移 | `false` |
| `--path` | HTTP 端点路径 | `/mcp` |

### API 模式

当设置 `--api-url` 时，MCP 服务器通过 HTTP API 调用远程 M-flow 实例：

```bash
cd m_flow-mcp
python -m src.server \
  --transport sse \
  --api-url http://mflow-backend:8000/api/v1 \
  --api-token your-token
```

> **注意**：API 模式下 `memorize_status`、`learn`、`query` 和 `prune` 已支持远程调用；如果传入 `episode_ids`，`learn` 仍会在远程模式下报 `NotImplementedError`。
>
> **后台任务**：`memorize` 和 `save_interaction` 现在会返回 `task_id`。传入 `wait=true` 时会同步等待任务完成；默认后台执行时，可通过 `memorize_status(task_id=...)` 查询最终成功或失败状态。

## IDE 集成

### Cursor

在 Cursor 设置中添加 MCP 服务器：

```json
{
  "mcpServers": {
    "m_flow": {
      "url": "http://localhost:8001/sse",
      "transport": "sse"
    }
  }
}
```

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "m_flow": {
      "command": "python",
      "args": ["-m", "src.server", "--transport", "stdio"],
      "cwd": "/path/to/mflow-main"
    }
  }
}
```

### VS Code + Continue

编辑 `~/.continue/config.json`：

```json
{
  "mcpServers": [
    {
      "name": "m_flow",
      "transport": {
        "type": "sse",
        "url": "http://localhost:8001/sse"
      }
    }
  ]
}
```

## RecallMode 说明

搜索时可用的召回模式：

| 模式 | 说明 | 使用场景 |
|------|------|----------|
| `TRIPLET_COMPLETION` | 三元组补全 + LLM 回答 | 一般知识查询 |
| `CHUNKS_LEXICAL` | 词法搜索 | 精确文本匹配 |
| `EPISODIC` | 情景记忆检索 | 事件/经历查询 |
| `PROCEDURAL` | 程序性记忆检索 | 操作步骤查询 |
| `CYPHER` | 图查询 | 复杂关系查询（高级用户） |

## 架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ AI Assistant│────▶│ MCP Protocol│────▶│ MCP Server  │────▶│ M-flow Core │
│ (Cursor等)  │◀────│ (stdio/sse) │◀────│             │◀────│ (知识图谱)  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

MCP Server 处理：
- 工具发现和参数验证
- 请求转发到 M-flow 核心
- 异步执行和状态管理
- 结构化响应格式化

## 测试

### 单元测试

```bash
# 运行完整单元测试
python -m m_flow_mcp.src.test_client

# 或直接运行
python m_flow-mcp/src/test_client.py
```

当前测试覆盖 **24 个测试用例**，包括：
- 11 个 MCP 工具的基础功能测试
- 参数验证测试（datasets、mode、top_k 等）
- 错误处理测试（无效 UUID、无效模式等）
- 工具函数测试

### 集成测试

```bash
# 运行集成测试
python -m m_flow_mcp.src.test_integration
```

集成测试包括：
- `test_full_workflow` - 完整工作流程（入库→查询→搜索→清理）
- `test_dataset_isolation` - 数据集隔离验证
- `test_error_recovery` - 错误恢复测试
- `test_concurrent_operations` - 并发操作测试
- `test_api_mode_handling` - API 模式处理测试

### 端到端测试

```bash
# 完整端到端测试（包含 Docker 构建和启动）
./m_flow-mcp/test_e2e.sh

# 快速测试（跳过长时间等待）
./m_flow-mcp/test_e2e.sh --quick

# 仅清理
./m_flow-mcp/test_e2e.sh --cleanup
```

端到端测试验证：
- Docker 镜像构建
- 服务启动和健康检查
- SSE 端点连通性
- 环境变量传递
- 日志无严重错误

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 语法检查
python -m py_compile m_flow-mcp/src/server.py
python -m py_compile m_flow-mcp/src/test_client.py
python -m py_compile m_flow-mcp/src/test_integration.py
```

## Docker

```bash
# 构建镜像
docker build -t mflow-mcp -f m_flow-mcp/Dockerfile .

# 运行容器（端口映射 8001:8000 与 docker compose 一致）
docker run -p 8001:8000 \
  -e TRANSPORT_MODE=sse \
  -e MCP_LOG_LEVEL=info \
  mflow-mcp

# 健康检查
curl http://localhost:8001/health
```

## 故障排除

### MCP Server 无法连接

```bash
# 检查服务状态
curl http://localhost:8001/health

# 查看日志
docker logs m_flow-mcp
```

### IDE 无法发现工具

1. 重启 IDE
2. 检查 MCP 配置文件 JSON 语法
3. 验证 URL 和端口是否正确

### 搜索无结果

1. 确认已有数据（使用 `memorize` 添加）
2. 使用 `list_data` 检查数据集
3. 尝试不同的 `recall_mode`

## 另请参阅

- [MCP 集成指南](../docs/guides/integrations/mcp.md)
- [M-flow 核心文档](../docs/index.md)
