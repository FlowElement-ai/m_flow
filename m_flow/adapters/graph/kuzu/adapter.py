"""
Kùzu embedded graph database adapter.

Provides async interface for graph operations using the Kùzu Python driver.
Supports local file storage and optional S3 synchronization.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type, Union
from uuid import NAMESPACE_OID, UUID, uuid5

from kuzu import Connection
from kuzu.database import Database

from m_flow.adapters.cache.config import get_cache_config
from m_flow.adapters.graph.graph_db_interface import (
    GraphProvider,
    record_graph_changes,
)
from m_flow.core import MemoryNode
from m_flow.exceptions import BadInputError
from m_flow.shared.files.storage import get_file_storage
from m_flow.shared.infra_utils.run_sync import run_sync
from m_flow.shared.logging_utils import get_logger
from m_flow.storage.utils_mod.utils import JSONEncoder

if TYPE_CHECKING:
    pass

_log = get_logger()

_cache_cfg = get_cache_config()
if _cache_cfg.shared_kuzu_lock:
    from m_flow.adapters.cache.get_cache_engine import get_cache_engine


def _utc_now_str() -> str:
    """Get current UTC timestamp formatted for Kùzu."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _ms_to_utc_str(ms_timestamp: int) -> str:
    """Convert millisecond timestamp to UTC datetime string for Kùzu."""
    dt = datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to millisecond timestamp."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _dump_props(obj: Any) -> str:
    """Serialize properties to JSON string."""
    return json.dumps(obj, cls=JSONEncoder)


def _parse_props_json(raw: str) -> dict:
    """Parse JSON properties, returning empty dict on failure."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _merge_node_props(data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge nested properties field into top-level dict, preserving column fields.

    Column values (created_at, updated_at) take priority over properties JSON values.
    Datetime values from Kuzu are converted to millisecond timestamps for API compatibility.
    """
    col_created_at = data.pop("created_at", None)
    col_updated_at = data.pop("updated_at", None)

    if "properties" in data and data["properties"]:
        try:
            nested = json.loads(data["properties"])
            del data["properties"]
            data.update(nested)
        except (json.JSONDecodeError, TypeError):
            _log.warning(f"Failed parsing node properties for {data.get('id')}")

    if col_created_at is not None:
        if isinstance(col_created_at, datetime):
            data["created_at"] = _datetime_to_ms(col_created_at)
        else:
            data["created_at"] = col_created_at
    if col_updated_at is not None:
        if isinstance(col_updated_at, datetime):
            data["updated_at"] = _datetime_to_ms(col_updated_at)
        else:
            data["updated_at"] = col_updated_at

    return data


def _partition_edges_by_endpoints(
    edges: List[Tuple[str, str, str, Dict[str, Any]]],
) -> List[List[Tuple[str, str, str, Dict[str, Any]]]]:
    """Partition edges so no two edges in the same batch share an endpoint.

    Kuzu's UNWIND+MERGE internally modifies endpoint node rows (adjacency
    lists). Two MERGE operations touching the same endpoint within a single
    statement trigger a write-write conflict. Splitting edges into batches
    where each batch has no shared endpoints avoids this.

    Algorithm: greedy placement — for each edge, find the first batch whose
    node set contains neither src nor tgt. O(E*B) where B is typically small.
    """
    if not edges:
        return []

    batches: List[Tuple[set, List]] = []  # [(graph_scope, edge_list), ...]

    for edge in edges:
        src, tgt = str(edge[0]), str(edge[1])
        placed = False
        for batch_nodes, batch_edges in batches:
            if src not in batch_nodes and tgt not in batch_nodes:
                batch_nodes.add(src)
                batch_nodes.add(tgt)
                batch_edges.append(edge)
                placed = True
                break
        if not placed:
            batches.append(({src, tgt}, [edge]))

    return [b[1] for b in batches]


class KuzuAdapter(GraphProvider):
    """
    Async Kùzu embedded graph database adapter.

    Implements GraphProvider for Kùzu, supporting local file-based
    storage with optional Redis-based distributed locking and S3 sync.
    """

    def __init__(self, db_path: str):
        """
        Initialize Kùzu database connection.

        Args:
            db_path: Path to database directory (local or s3:// prefix)
        """
        self._path = db_path
        self._db: Optional[Database] = None
        self._conn: Optional[Connection] = None
        self._active_conns = 0
        self._closed = False

        # Lock for connection state changes
        self._conn_lock = asyncio.Lock()
        # Lock for serializing queries (Kùzu connection is not thread-safe)
        self._query_lock = asyncio.Lock()

        if _cache_cfg.shared_kuzu_lock:
            lock_id = str(uuid5(NAMESPACE_OID, db_path))
            self._redis_lock = get_cache_engine(lock_key=f"kuzu-lock-{lock_id}")
        else:
            self._executor = ThreadPoolExecutor()
            self._bootstrap_connection()

    @property
    def db_path(self) -> str:
        return self._path

    @property
    def db(self) -> Optional[Database]:
        return self._db

    @property
    def connection(self) -> Optional[Connection]:
        return self._conn

    def _bootstrap_connection(self) -> None:
        """Initialize database connection and schema."""
        self._setup_json_ext()
        self._create_db_instance()
        self._create_schema()
        _log.debug("Kùzu database initialized")

    def _setup_json_ext(self) -> None:
        """Install JSON extension using temporary database."""
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=True) as tf:
                tmp_db = Database(
                    tf.name,
                    buffer_pool_size=4096 * 1024 * 1024,
                    max_db_size=4096 * 1024 * 1024,
                )
                tmp_db.init_database()
                tmp_conn = Connection(tmp_db)
                tmp_conn.execute("INSTALL JSON;")
        except Exception as err:
            _log.info(f"JSON extension setup skipped: {err}")

    def _create_db_instance(self) -> None:
        """Create Kùzu database instance."""
        try:
            if self._path.startswith("s3://"):
                self._handle_s3_init()
            else:
                self._handle_local_init()

            self._db.init_database()
            self._conn = Connection(self._db)

            try:
                self._conn.execute("LOAD EXTENSION JSON;")
                _log.info("JSON extension loaded")
            except Exception as err:
                _log.info(f"JSON extension load skipped: {err}")

        except Exception as err:
            _log.error(f"Kùzu init failed: {err}")
            raise

    def _handle_s3_init(self) -> None:
        """Initialize from S3 storage."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
            self._temp_file = tf.name

        run_sync(self._sync_from_s3())

        self._db = Database(
            self._temp_file,
            buffer_pool_size=8192 * 1024 * 1024,
            max_db_size=16384 * 1024 * 1024,
        )

    def _handle_local_init(self) -> None:
        """Initialize local database.

        auto_checkpoint is disabled to avoid excessive WAL flushes during
        sequential add+memorize loops. Callers (e.g. memorize()) invoke
        checkpoint() explicitly when needed.
        """
        parent = os.path.dirname(self._path)
        if not parent:
            parent = os.path.dirname(os.path.abspath(self._path))

        storage = get_file_storage(parent)
        run_sync(storage.ensure_directory_exists())

        _buffer_pool_mb = int(os.getenv("KUZU_BUFFER_POOL_MB", "4096"))
        _max_db_mb = int(os.getenv("KUZU_MAX_DB_MB", "32768"))

        # Proactively clean stale locks before attempting to open
        self._cleanup_stale_locks()

        try:
            self._db = Database(
                self._path,
                buffer_pool_size=_buffer_pool_mb * 1024 * 1024,
                max_db_size=_max_db_mb * 1024 * 1024,
            )
            self._db.auto_checkpoint = False
        except RuntimeError as e:
            if "lock" in str(e).lower():
                _log.warning(f"Kuzu lock still present after cleanup: {e}")
                # Try more aggressive cleanup
                self._cleanup_stale_locks(aggressive=True)
                try:
                    self._db = Database(
                        self._path,
                        buffer_pool_size=_buffer_pool_mb * 1024 * 1024,
                        max_db_size=_max_db_mb * 1024 * 1024,
                    )
                    self._db.auto_checkpoint = False
                    _log.info("Kuzu database opened after aggressive lock cleanup")
                    return
                except RuntimeError:
                    pass
            self._try_migrate_db()
            self._db = Database(
                self._path,
                buffer_pool_size=_buffer_pool_mb * 1024 * 1024,
                max_db_size=_max_db_mb * 1024 * 1024,
            )
            self._db.auto_checkpoint = False

    def _cleanup_stale_locks(self, aggressive: bool = False) -> None:
        """Remove stale lock files from Kuzu database directory.

        Kuzu uses file locks that may persist after abnormal process termination.
        This method removes these locks to allow the database to be reopened.

        Kuzu lock file locations:
        - Inside db directory: .lock*, *.lock, .wal, wal/
        - Outside db directory (parent): {db_name}.wal, {db_name}.lock

        Args:
            aggressive: If True, also removes WAL files and other recovery files.
        """
        import glob
        import shutil

        lock_patterns = []

        # Patterns inside database directory (if it exists)
        if os.path.isdir(self._path):
            lock_patterns.extend(
                [
                    os.path.join(self._path, ".lock*"),
                    os.path.join(self._path, "*.lock"),
                    os.path.join(self._path, ".wal"),
                ]
            )
            if aggressive:
                lock_patterns.extend(
                    [
                        os.path.join(self._path, "wal"),
                        os.path.join(self._path, ".tmp*"),
                        os.path.join(self._path, "*.tmp"),
                    ]
                )

        # Patterns outside database directory (sibling files)
        # Kuzu creates {db_path}.wal and {db_path}.lock in parent directory
        parent_dir = os.path.dirname(self._path)
        db_name = os.path.basename(self._path)
        if parent_dir and os.path.isdir(parent_dir):
            lock_patterns.extend(
                [
                    os.path.join(parent_dir, f"{db_name}.wal"),
                    os.path.join(parent_dir, f"{db_name}.lock"),
                    os.path.join(parent_dir, f"{db_name}.lock*"),
                ]
            )

        for pattern in lock_patterns:
            for lock_file in glob.glob(pattern):
                try:
                    if os.path.isdir(lock_file):
                        shutil.rmtree(lock_file)
                        _log.info(f"Removed stale lock directory: {lock_file}")
                    else:
                        os.remove(lock_file)
                        _log.info(f"Removed stale lock file: {lock_file}")
                except OSError as e:
                    _log.warning(f"Failed to remove lock {lock_file}: {e}")

    def _try_migrate_db(self) -> None:
        """Attempt database version migration if needed."""
        from .kuzu_migrate import read_kuzu_storage_version
        import kuzu

        stored_ver = read_kuzu_storage_version(self._path)
        if stored_ver in ("0.9.0", "0.8.2") and stored_ver != kuzu.__version__:
            from .kuzu_migrate import kuzu_migration

            kuzu_migration(
                new_db=f"{self._path}_new",
                old_db=self._path,
                new_version=kuzu.__version__,
                old_version=stored_ver,
                overwrite=True,
            )

    def _create_schema(self) -> None:
        """Create node and edge tables if not exist."""
        self._conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Node(
                id STRING PRIMARY KEY,
                name STRING,
                type STRING,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                properties STRING
            )
        """)
        self._conn.execute("""
            CREATE REL TABLE IF NOT EXISTS EDGE(
                FROM Node TO Node,
                relationship_name STRING,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                properties STRING
            )
        """)

    async def _sync_to_s3(self) -> None:
        """Push local database to S3 storage."""
        if os.getenv("STORAGE_BACKEND", "").lower() != "s3":
            return
        if not hasattr(self, "_temp_file"):
            return

        from m_flow.shared.files.storage.S3FileStorage import S3FileStorage

        s3 = S3FileStorage("")

        if self._conn:
            async with self._query_lock:
                self._conn.execute("CHECKPOINT;")

        s3.s3.put(self._temp_file, self._path, recursive=True)

    async def _sync_from_s3(self) -> None:
        """Pull database from S3 storage."""
        from m_flow.shared.files.storage.S3FileStorage import S3FileStorage

        s3 = S3FileStorage("")
        try:
            s3.s3.get(self._path, self._temp_file, recursive=True)
        except FileNotFoundError:
            _log.warning(f"S3 database not found: {self._path}")

    # Compatibility aliases
    async def sync_to_remote(self) -> None:
        await self._sync_to_s3()

    async def pull_from_s3(self) -> None:
        await self._sync_from_s3()

    async def checkpoint(self) -> None:
        """Force WAL checkpoint to persist data to disk.

        Kuzu uses Write-Ahead Logging (WAL) by default. Data written to WAL
        may not be immediately persisted to the main database file. This method
        forces a checkpoint to ensure all data is durably stored.

        Should be called after critical write operations (e.g., after memorize)
        to prevent data loss on abnormal shutdown.
        """
        try:
            if self._conn:
                async with self._query_lock:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        getattr(self, "_executor", None), lambda: self._conn.execute("CHECKPOINT;")
                    )
                _log.info("Kuzu checkpoint completed - data persisted to disk")
        except Exception as err:
            _log.warning(f"Kuzu checkpoint failed: {err}")

    def close(self) -> None:
        """Close database connections with automatic checkpoint."""
        try:
            # Force checkpoint before closing to persist WAL data
            if self._conn:
                try:
                    self._conn.execute("CHECKPOINT;")
                    _log.debug("Checkpoint executed before close")
                except Exception as e:
                    _log.warning(f"Checkpoint before close failed: {e}")
                del self._conn
                self._conn = None
            if self._db:
                del self._db
                self._db = None
        except Exception as e:
            _log.warning(f"Error during close: {e}")
        self._closed = True
        _log.info("Kùzu database closed")

    def reopen(self) -> None:
        """Reopen closed database."""
        if self._closed:
            self._closed = False
            self._bootstrap_connection()
            _log.info("Kùzu database reopened")

    @asynccontextmanager
    async def get_session(self):
        """Provide session context (Kùzu uses connection directly)."""
        try:
            yield self._conn
        finally:
            pass

    # =========================================================================
    # Query Execution
    # =========================================================================

    async def query(self, cypher: str, params: Optional[dict] = None) -> List[Tuple]:
        """
        Execute Cypher query asynchronously.

        Args:
            cypher: Cypher query string
            params: Query parameters

        Returns:
            List of result tuples
        """
        loop = asyncio.get_running_loop()
        params = params or {}

        def _exec_blocking():
            lock_held = False
            try:
                if _cache_cfg.shared_kuzu_lock:
                    self._redis_lock.acquire_lock()
                    lock_held = True

                if not self._conn:
                    _log.info("Reconnecting to Kùzu...")
                    self._bootstrap_connection()

                cursor = self._conn.execute(cypher, params)
                rows = []
                while cursor.has_next():
                    row = cursor.get_next()
                    processed = []
                    for val in row:
                        if hasattr(val, "as_py"):
                            val = val.as_py()
                        processed.append(val)
                    rows.append(tuple(processed))
                return rows

            except Exception as err:
                _log.error(f"Query failed: {err}")
                raise
            finally:
                if _cache_cfg.shared_kuzu_lock and lock_held:
                    try:
                        self.close()
                    finally:
                        self._redis_lock.release_lock()

        if _cache_cfg.shared_kuzu_lock:
            async with self._conn_lock:
                self._active_conns += 1
                _log.info(f"Active connections: {self._active_conns}")
                try:
                    result = _exec_blocking()
                finally:
                    self._active_conns -= 1
                    _log.info(f"Active connections: {self._active_conns}")
                return result
        else:
            async with self._query_lock:
                return await loop.run_in_executor(self._executor, _exec_blocking)

    async def is_empty(self) -> bool:
        """Check if database contains any nodes."""
        result = await self.query("MATCH (n) RETURN true LIMIT 1;")
        return len(result) == 0

    # =========================================================================
    # Node Operations
    # =========================================================================

    async def has_node(self, node_id: str) -> bool:
        """Check if node exists."""
        result = await self.query("MATCH (n:Node) WHERE n.id = $id RETURN COUNT(n) > 0", {"id": node_id})
        return result[0][0] if result else False

    async def add_node(self, node: MemoryNode) -> None:
        """Insert or update single node."""
        try:
            props = node.model_dump() if hasattr(node, "model_dump") else vars(node)

            core = {
                "id": str(props.pop("id", "")),
                "name": str(props.pop("name", "")),
                "type": str(props.pop("type", "")),
            }

            node_created_at = props.pop("created_at", None)
            props.pop("updated_at", None)

            now = _utc_now_str()
            created_at_str = _ms_to_utc_str(node_created_at) if node_created_at is not None else now

            cypher = """
            MERGE (n:Node {id: $id})
            ON CREATE SET
                n.name = $name,
                n.type = $type,
                n.properties = $props,
                n.created_at = timestamp($created_at),
                n.updated_at = timestamp($updated_at)
            ON MATCH SET
                n.name = $name,
                n.type = $type,
                n.properties = $props,
                n.updated_at = timestamp($updated_at)
            """

            await self.query(
                cypher,
                {
                    "id": core["id"],
                    "name": core["name"],
                    "type": core["type"],
                    "props": _dump_props(props),
                    "created_at": created_at_str,
                    "updated_at": now,
                },
            )

        except Exception as err:
            _log.error(f"add_node failed: {err}")
            raise

    @record_graph_changes
    async def add_nodes(self, nodes: List[MemoryNode]) -> None:
        """Bulk insert/update nodes with write-conflict fallback."""
        if not nodes:
            return

        now = _utc_now_str()
        items_by_id = {}

        for n in nodes:
            props = n.model_dump() if hasattr(n, "model_dump") else vars(n)

            node_created_at = props.pop("created_at", None)
            props.pop("updated_at", None)

            created_at_str = _ms_to_utc_str(node_created_at) if node_created_at is not None else now

            node_id = str(props.pop("id", ""))
            items_by_id[node_id] = {
                "id": node_id,
                "name": str(props.pop("name", "")),
                "type": str(props.pop("type", "")),
                "properties": _dump_props(props),
                "created_at": created_at_str,
                "updated_at": now,
            }

        items = list(items_by_id.values())

        if len(items) < len(nodes):
            _log.info(f"add_nodes: deduped {len(nodes)} → {len(items)} ({len(nodes) - len(items)} duplicates removed)")

        cypher_bulk = """
        UNWIND $items AS item
        MERGE (n:Node {id: item.id})
        ON CREATE SET
            n.name = item.name,
            n.type = item.type,
            n.properties = item.properties,
            n.created_at = timestamp(item.created_at),
            n.updated_at = timestamp(item.updated_at)
        ON MATCH SET
            n.name = item.name,
            n.type = item.type,
            n.properties = item.properties,
            n.updated_at = timestamp(item.updated_at)
        """

        try:
            await self.query(cypher_bulk, {"items": items})
            _log.debug(f"Processed {len(items)} nodes")
        except RuntimeError as err:
            err_str = str(err).lower()
            is_recoverable = "write-write conflict" in err_str or ("duplicat" in err_str and "key" in err_str)
            if not is_recoverable:
                _log.error(f"add_nodes batch failed: {err}")
                raise
            _log.warning(
                f"add_nodes: batch conflict on bulk MERGE ({len(items)} nodes), falling back to sequential writes"
            )
            cypher_single = """
            MERGE (n:Node {id: $id})
            ON CREATE SET
                n.name = $name,
                n.type = $type,
                n.properties = $properties,
                n.created_at = timestamp($created_at),
                n.updated_at = timestamp($updated_at)
            ON MATCH SET
                n.name = $name,
                n.type = $type,
                n.properties = $properties,
                n.updated_at = timestamp($updated_at)
            """
            failed = 0
            for item in items:
                try:
                    await self.query(cypher_single, item)
                except Exception as e2:
                    failed += 1
                    _log.warning(f"add_nodes sequential write failed for {item['id'][:20]}: {e2}")
            if failed:
                _log.warning(f"add_nodes: {failed}/{len(items)} nodes failed in sequential fallback")
            else:
                _log.info(f"add_nodes: sequential fallback completed successfully ({len(items)} nodes)")

    async def delete_node(self, node_id: str) -> None:
        """Remove node and its edges."""
        await self.query("MATCH (n:Node) WHERE n.id = $id DETACH DELETE n", {"id": node_id})

    async def delete_nodes(self, node_ids: List[str]) -> None:
        """Remove multiple nodes and their edges."""
        await self.query("MATCH (n:Node) WHERE n.id IN $ids DETACH DELETE n", {"ids": node_ids})

    async def update_node(self, node_id: str, props: Dict[str, Any]) -> None:
        """Merge props into an existing node's properties JSON (read-modify-write)."""
        read_q = "MATCH (n:Node {id: $id}) RETURN n.properties AS props"
        rows = await self.query(read_q, {"id": node_id})
        if not rows:
            return

        existing = _parse_props_json(rows[0][0]) if rows[0][0] else {}
        existing.update(props)

        write_q = "MATCH (n:Node {id: $id}) SET n.properties = $props"
        await self.query(write_q, {"id": node_id, "props": _dump_props(existing)})

    async def delete_edge(self, src: str, dst: str, rel: str) -> None:
        """Remove a specific directed edge."""
        cypher = "MATCH (a:Node {id: $src})-[r:EDGE]->(b:Node {id: $dst}) WHERE r.relationship_name = $rel DELETE r"
        await self.query(cypher, {"src": src, "dst": dst, "rel": rel})

    async def extract_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get single node by ID."""
        cypher = """
        MATCH (n:Node) WHERE n.id = $id
        RETURN {
            id: n.id,
            name: n.name,
            type: n.type,
            properties: n.properties,
            created_at: n.created_at,
            updated_at: n.updated_at
        }
        """
        try:
            result = await self.query(cypher, {"id": node_id})
            if result and result[0]:
                return _merge_node_props(result[0][0])
            return None
        except Exception as err:
            _log.error(f"extract_node failed for {node_id}: {err}")
            return None

    async def extract_nodes(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """Get multiple nodes by IDs."""
        cypher = """
        MATCH (n:Node) WHERE n.id IN $ids
        RETURN {
            id: n.id,
            name: n.name,
            type: n.type,
            properties: n.properties,
            created_at: n.created_at,
            updated_at: n.updated_at
        }
        """
        try:
            results = await self.query(cypher, {"ids": node_ids})
            return [_merge_node_props(r[0]) for r in results if r[0]]
        except Exception as err:
            _log.error(f"extract_nodes failed: {err}")
            return []

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get single node by ID."""
        return await self.extract_node(node_id)

    async def get_nodes(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """Get multiple nodes by IDs."""
        return await self.extract_nodes(node_ids)

    # =========================================================================
    # Edge Operations
    # =========================================================================

    async def has_edge(self, from_node: str, to_node: str, edge_label: str) -> bool:
        """Check if edge exists."""
        cypher = """
        MATCH (a:Node)-[r:EDGE]->(b:Node)
        WHERE a.id = $src AND b.id = $tgt AND r.relationship_name = $lbl
        RETURN COUNT(r) > 0
        """
        result = await self.query(
            cypher,
            {
                "src": from_node,
                "tgt": to_node,
                "lbl": edge_label,
            },
        )
        return result[0][0] if result else False

    async def has_edges(self, edges: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
        """Check which edges exist."""
        if not edges:
            return []

        try:
            items = [{"src": str(e[0]), "tgt": str(e[1]), "rel": str(e[2])} for e in edges]

            cypher = """
            UNWIND $items AS item
            MATCH (a:Node)-[r:EDGE]->(b:Node)
            WHERE a.id = item.src AND b.id = item.tgt AND r.relationship_name = item.rel
            RETURN a.id, b.id, r.relationship_name
            """

            results = await self.query(cypher, {"items": items})
            existing = [(str(r[0]), str(r[1]), str(r[2])) for r in results]
            _log.debug(f"Found {len(existing)}/{len(edges)} edges")
            return existing

        except Exception as err:
            _log.error(f"has_edges failed: {err}")
            return []

    async def add_edge(
        self,
        from_node: str,
        to_node: str,
        relationship_name: str,
        edge_properties: Dict[str, Any] = None,
    ) -> None:
        """Insert or update single edge.

        Warning: If either source or target node doesn't exist in the graph,
        the MATCH returns empty and MERGE silently does nothing (no exception).
        A warning is logged in this case.
        """
        try:
            now = _utc_now_str()
            cypher = """
            MATCH (a:Node), (b:Node)
            WHERE a.id = $src AND b.id = $tgt
            MERGE (a)-[r:EDGE {relationship_name: $rel}]->(b)
            ON CREATE SET
                r.created_at = timestamp($ts),
                r.updated_at = timestamp($ts),
                r.properties = $props
            ON MATCH SET
                r.updated_at = timestamp($ts),
                r.properties = $props
            RETURN COUNT(*) AS cnt
            """

            result = await self.query(
                cypher,
                {
                    "src": from_node,
                    "tgt": to_node,
                    "rel": relationship_name,
                    "ts": now,
                    "props": _dump_props(edge_properties or {}),
                },
            )

            # Detect silent failure: MATCH found no nodes → MERGE did nothing
            rows_affected = 0
            if result:
                first = result[0]
                if isinstance(first, (list, tuple)) and first:
                    rows_affected = first[0]
                elif isinstance(first, dict):
                    rows_affected = first.get("cnt", 0)
                elif isinstance(first, (int, float)):
                    rows_affected = int(first)

            if rows_affected == 0:
                _log.warning(
                    f"add_edge silent no-op: edge '{relationship_name}' "
                    f"from={from_node[:20]}... to={to_node[:20]}... — "
                    f"one or both nodes may not exist in graph"
                )

        except Exception as err:
            _log.error(f"add_edge failed: {err}")
            raise

    @record_graph_changes
    async def add_edges(self, edges: List[Tuple[str, str, str, Dict[str, Any]]]) -> None:
        """Bulk insert/update edges using endpoint-partitioned batches.

        Kuzu's UNWIND+MERGE conflicts when two edges in the same statement
        share an endpoint node. We partition edges into batches where no two
        edges share an endpoint, then execute each batch as a separate MERGE.
        A sequential fallback is kept as a safety net for unforeseen issues.
        """
        if not edges:
            return

        seen = {}
        for e in edges:
            key = (str(e[0]), str(e[1]), str(e[2]))
            seen[key] = e
        deduped = list(seen.values())

        if len(deduped) < len(edges):
            _log.info(
                f"add_edges: deduped {len(edges)} → {len(deduped)} ({len(edges) - len(deduped)} duplicates removed)"
            )
        edges = deduped

        partitions = _partition_edges_by_endpoints(edges)
        now = _utc_now_str()

        cypher_bulk = """
        UNWIND $items AS item
        MATCH (a:Node), (b:Node)
        WHERE a.id = item.src AND b.id = item.tgt
        MERGE (a)-[r:EDGE {relationship_name: item.rel}]->(b)
        ON CREATE SET
            r.created_at = timestamp(item.ts),
            r.updated_at = timestamp(item.ts),
            r.properties = item.props
        ON MATCH SET
            r.updated_at = timestamp(item.ts),
            r.properties = item.props
        """

        if len(partitions) > 1:
            _log.debug(f"add_edges: {len(edges)} edges split into {len(partitions)} endpoint-partitioned batches")

        for batch_idx, batch in enumerate(partitions):
            items = [
                {
                    "src": e[0],
                    "tgt": e[1],
                    "rel": e[2],
                    "props": _dump_props(e[3]),
                    "ts": now,
                }
                for e in batch
            ]
            try:
                await self.query(cypher_bulk, {"items": items})
            except RuntimeError as err:
                err_str = str(err).lower()
                is_recoverable = "write-write conflict" in err_str or ("duplicat" in err_str and "key" in err_str)
                if not is_recoverable:
                    _log.error(f"add_edges batch {batch_idx} failed: {err}")
                    raise
                _log.warning(
                    f"add_edges: batch conflict in partitioned batch "
                    f"{batch_idx} ({len(items)} edges), falling back to sequential"
                )
                cypher_single = """
                MATCH (a:Node), (b:Node)
                WHERE a.id = $src AND b.id = $tgt
                MERGE (a)-[r:EDGE {relationship_name: $rel}]->(b)
                ON CREATE SET
                    r.created_at = timestamp($ts),
                    r.updated_at = timestamp($ts),
                    r.properties = $props
                ON MATCH SET
                    r.updated_at = timestamp($ts),
                    r.properties = $props
                """
                for item in items:
                    try:
                        await self.query(cypher_single, item)
                    except Exception as e2:
                        _log.warning(f"add_edges sequential write failed: {e2}")

    async def get_edges(self, node_id: str) -> List[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
        """Get all edges for a node."""
        cypher = """
        MATCH (n:Node)-[r]-(m:Node) WHERE n.id = $id
        RETURN
            {id: n.id, name: n.name, type: n.type, properties: n.properties, created_at: n.created_at, updated_at: n.updated_at},
            r.relationship_name,
            {id: m.id, name: m.name, type: m.type, properties: m.properties, created_at: m.created_at, updated_at: m.updated_at}
        """
        try:
            results = await self.query(cypher, {"id": node_id})
            edges = []
            for row in results:
                if row and len(row) == 3:
                    src = _merge_node_props(row[0])
                    tgt = _merge_node_props(row[2])
                    edges.append((src, row[1], tgt))
            return edges
        except Exception as err:
            _log.error(f"get_edges failed for {node_id}: {err}")
            return []

    # =========================================================================
    # Graph Traversal
    # =========================================================================

    async def get_neighbors(self, node_id: str) -> List[Dict[str, Any]]:
        """Get all directly connected nodes."""
        return await self.get_neighbours(node_id)

    async def get_neighbours(self, node_id: str) -> List[Dict[str, Any]]:
        """Get all directly connected nodes (alternate spelling)."""
        cypher = "MATCH (n)-[r]-(m) WHERE n.id = $id RETURN DISTINCT properties(m)"
        try:
            result = await self.query(cypher, {"id": node_id})
            return [row[0] for row in result] if result else []
        except Exception as err:
            _log.error(f"get_neighbours failed for {node_id}: {err}")
            return []

    async def get_predecessors(
        self, node_id: Union[str, UUID], edge_label: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get nodes pointing to this node."""
        try:
            if edge_label:
                cypher = """
                MATCH (n)<-[r:EDGE]-(m)
                WHERE n.id = $id AND r.relationship_name = $lbl
                RETURN properties(m)
                """
                params = {"id": str(node_id), "lbl": edge_label}
            else:
                cypher = "MATCH (n)<-[r:EDGE]-(m) WHERE n.id = $id RETURN properties(m)"
                params = {"id": str(node_id)}

            result = await self.query(cypher, params)
            return [row[0] for row in result] if result else []
        except Exception as err:
            _log.error(f"get_predecessors failed for {node_id}: {err}")
            return []

    async def get_successors(self, node_id: Union[str, UUID], edge_label: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get nodes this node points to."""
        try:
            if edge_label:
                cypher = """
                MATCH (n)-[r:EDGE]->(m)
                WHERE n.id = $id AND r.relationship_name = $lbl
                RETURN properties(m)
                """
                params = {"id": str(node_id), "lbl": edge_label}
            else:
                cypher = "MATCH (n)-[r:EDGE]->(m) WHERE n.id = $id RETURN properties(m)"
                params = {"id": str(node_id)}

            result = await self.query(cypher, params)
            return [row[0] for row in result] if result else []
        except Exception as err:
            _log.error(f"get_successors failed for {node_id}: {err}")
            return []

    async def get_triplets(self, node_id: str) -> List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
        """Get all connected node-edge-node tuples."""
        cypher = """
        MATCH (n:Node)-[r:EDGE]-(m:Node) WHERE n.id = $id
        RETURN
            {id: n.id, name: n.name, type: n.type, properties: n.properties, created_at: n.created_at, updated_at: n.updated_at},
            {relationship_name: r.relationship_name, properties: r.properties},
            {id: m.id, name: m.name, type: m.type, properties: m.properties, created_at: m.created_at, updated_at: m.updated_at}
        """
        try:
            results = await self.query(cypher, {"id": node_id})
            conns = []
            for row in results:
                if row and len(row) == 3:
                    processed = []
                    for item in row:
                        if isinstance(item, dict) and "properties" in item:
                            processed.append(_merge_node_props(item.copy()))
                        else:
                            processed.append(item)
                    conns.append(tuple(processed))
            return conns
        except Exception as err:
            _log.error(f"get_triplets failed for {node_id}: {err}")
            return []

    async def get_disconnected_nodes(self) -> List[str]:
        """Get nodes with no edges."""
        cypher = "MATCH (n:Node) WHERE NOT EXISTS((n)-[]-()) RETURN n.id"
        result = await self.query(cypher)
        return [str(row[0]) for row in result]

    async def remove_connection_to_predecessors_of(self, node_ids: List[str], edge_label: str) -> None:
        """Remove incoming edges with given label."""
        cypher = """
        MATCH (n)<-[r:EDGE]-(m)
        WHERE n.id IN $ids AND r.relationship_name = $lbl
        DELETE r
        """
        await self.query(cypher, {"ids": node_ids, "lbl": edge_label})

    async def remove_connection_to_successors_of(self, node_ids: List[str], edge_label: str) -> None:
        """Remove outgoing edges with given label."""
        cypher = """
        MATCH (n)-[r:EDGE]->(m)
        WHERE n.id IN $ids AND r.relationship_name = $lbl
        DELETE r
        """
        await self.query(cypher, {"ids": node_ids, "lbl": edge_label})

    # =========================================================================
    # Graph Data Retrieval
    # =========================================================================

    async def get_graph_data(
        self,
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Tuple[str, str, str, Dict[str, Any]]]]:
        """Get all nodes and edges."""
        t0 = time.time()

        try:
            node_cypher = """
            MATCH (n:Node)
            RETURN n.id, {name: n.name, type: n.type, properties: n.properties, created_at: n.created_at, updated_at: n.updated_at}
            """
            node_results = await self.query(node_cypher)

            nodes = []
            for r in node_results:
                if r[0]:
                    nid = str(r[0])
                    props = _merge_node_props(r[1])
                    nodes.append((nid, props))

            if not nodes:
                _log.warning("No nodes found")
                return [], []

            edge_cypher = """
            MATCH (n:Node)-[r]->(m:Node)
            RETURN n.id, m.id, r.relationship_name, r.properties
            """
            edge_results = await self.query(edge_cypher)

            edges = []
            for r in edge_results:
                if r and len(r) >= 3:
                    src, tgt, rel = str(r[0]), str(r[1]), str(r[2])
                    props = _parse_props_json(r[3]) if len(r) > 3 else {}
                    edges.append((src, tgt, rel, props))

            # Create self-loops if no edges exist
            if nodes and not edges:
                _log.debug("No edges found, adding self-references")
                for nid, _ in nodes:
                    edges.append((nid, nid, "SELF", {"relationship_name": "SELF", "vector_distance": 0.0}))

            _log.info(f"Retrieved {len(nodes)} nodes, {len(edges)} edges in {time.time() - t0:.2f}s")
            return nodes, edges

        except Exception as err:
            _log.error(f"get_graph_data failed: {err}")
            raise

    async def get_model_independent_graph_data(self) -> Dict[str, List[str]]:
        """Get graph schema info."""
        labels_result = await self.query("MATCH (n:Node) RETURN DISTINCT labels(n)")
        rels_result = await self.query("MATCH ()-[r:EDGE]->() RETURN DISTINCT r.relationship_name")
        return {
            "node_labels": [r[0] for r in labels_result],
            "relationship_types": [r[0] for r in rels_result],
        }

    async def get_id_filtered_graph_data(self, target_ids: list[str]):
        """Get graph data filtered by node IDs."""
        t0 = time.time()

        try:
            if not target_ids:
                _log.warning("Empty target_ids for filtered retrieval")
                return [], []

            if not all(isinstance(x, str) for x in target_ids):
                raise BadInputError("target_ids must be strings")

            cypher = """
            MATCH (n:Node)-[r]->(m:Node)
            WHERE n.id IN $ids OR m.id IN $ids
            RETURN n.id, {name: n.name, type: n.type, properties: n.properties, created_at: n.created_at, updated_at: n.updated_at},
                   m.id, {name: m.name, type: m.type, properties: m.properties, created_at: m.created_at, updated_at: m.updated_at},
                   r.relationship_name, r.properties
            """

            results = await self.query(cypher, {"ids": target_ids})

            if not results:
                _log.info("No data for supplied IDs")
                return [], []

            nodes_map = {}
            edges = []

            for n_id, n_props, m_id, m_props, rel, rel_props_raw in results:
                nodes_map[n_id] = (n_id, _merge_node_props(n_props))
                nodes_map[m_id] = (m_id, _merge_node_props(m_props))

                edge_props = _parse_props_json(rel_props_raw)
                src = edge_props.get("source_node_id", n_id)
                tgt = edge_props.get("target_node_id", m_id)
                edges.append((src, tgt, rel, edge_props))

            _log.info(f"ID-filtered: {len(nodes_map)} nodes, {len(edges)} edges in {time.time() - t0:.2f}s")
            return list(nodes_map.values()), edges

        except Exception as err:
            _log.error(f"ID-filtered retrieval failed: {err}")
            raise

    async def query_by_attributes(self, attribute_filters: List[Dict[str, List[Union[str, int]]]]):
        """Get nodes/edges filtered by attributes."""
        where_parts = []
        params = {}

        for i, flt in enumerate(attribute_filters):
            for attr, vals in flt.items():
                pname = f"vals_{i}_{attr}"
                where_parts.append(f"n.{attr} IN ${pname}")
                params[pname] = vals

        where_clause = " AND ".join(where_parts)

        node_cypher = f"""
        MATCH (n:Node) WHERE {where_clause}
        RETURN n.id, {{name: n.name, type: n.type, properties: n.properties, created_at: n.created_at, updated_at: n.updated_at}}
        """

        edge_cypher = f"""
        MATCH (n1:Node)-[r:EDGE]->(n2:Node)
        WHERE {where_clause.replace("n.", "n1.")} AND {where_clause.replace("n.", "n2.")}
        RETURN n1.id, n2.id, r.relationship_name, r.properties
        """

        node_results, edge_results = await asyncio.gather(
            self.query(node_cypher, params),
            self.query(edge_cypher, params),
        )

        nodes = []
        for r in node_results:
            if r[0]:
                nodes.append((str(r[0]), _merge_node_props(r[1])))

        if not nodes:
            _log.warning("No nodes matched filters")
            return [], []

        edges = []
        for r in edge_results:
            if r and len(r) >= 3:
                props = _parse_props_json(r[3]) if len(r) > 3 else {}
                edges.append((str(r[0]), str(r[1]), str(r[2]), props))

        return nodes, edges

    async def extract_typed_subgraph(
        self, node_type: Type[Any], node_name: List[str]
    ) -> Tuple[List[Tuple[str, dict]], List[Tuple[str, str, str, dict]]]:
        """Get subgraph for specific node names and type."""
        label = node_type.__name__

        # Find primary nodes
        primary_cypher = """
        UNWIND $names AS wanted
        MATCH (n:Node) WHERE n.type = $label AND n.name = wanted
        RETURN DISTINCT n.id
        """
        primary_rows = await self.query(primary_cypher, {"names": node_name, "label": label})
        primary_ids = [r[0] for r in primary_rows]

        if not primary_ids:
            return [], []

        # Find neighbors
        nbr_cypher = """
        MATCH (n:Node)-[:EDGE]-(nbr:Node) WHERE n.id IN $ids
        RETURN DISTINCT nbr.id
        """
        nbr_rows = await self.query(nbr_cypher, {"ids": primary_ids})
        nbr_ids = [r[0] for r in nbr_rows]

        all_ids = list({*primary_ids, *nbr_ids})

        # Get node details
        node_cypher = """
        MATCH (n:Node) WHERE n.id IN $ids
        RETURN n.id, n.name, n.type, n.properties, n.created_at, n.updated_at
        """
        node_rows = await self.query(node_cypher, {"ids": all_ids})

        nodes = []
        for nid, name, typ, props_raw, col_created_at, col_updated_at in node_rows:
            data = {"id": nid, "name": name, "type": typ}
            data.update(_parse_props_json(props_raw))
            if col_created_at is not None:
                data["created_at"] = (
                    _datetime_to_ms(col_created_at) if isinstance(col_created_at, datetime) else col_created_at
                )
            if col_updated_at is not None:
                data["updated_at"] = (
                    _datetime_to_ms(col_updated_at) if isinstance(col_updated_at, datetime) else col_updated_at
                )
            nodes.append((nid, data))

        # Get edges
        edge_cypher = """
        MATCH (a:Node)-[r:EDGE]-(b:Node) WHERE a.id IN $ids AND b.id IN $ids
        RETURN a.id, b.id, r.relationship_name, r.properties
        """
        edge_rows = await self.query(edge_cypher, {"ids": all_ids})

        edges = []
        for src, tgt, rel, props_raw in edge_rows:
            edges.append((src, tgt, rel, _parse_props_json(props_raw)))

        return nodes, edges

    async def get_nodeset_id_filtered_graph_data(
        self,
        node_type: Type[Any],
        node_name: List[str],
        target_ids: List[str],
    ) -> Tuple[List[Tuple[str, dict]], List[Tuple[str, str, str, dict]]]:
        """Get ID-filtered subgraph within a memory space."""
        label = node_type.__name__

        if not target_ids:
            return [], []

        if not all(isinstance(x, str) for x in target_ids):
            raise ValueError("target_ids must be strings")

        # Get memory space member IDs
        # NOTE: target_ids filtering is done in Python (not in Cypher) because
        # Kuzu's IN operator may behave differently with UUID-style strings
        # compared to Python set intersection. Verified by production testing.
        member_cypher = """
        UNWIND $names AS wanted
        MATCH (ns:Node)-[e:EDGE]-(member:Node)
        WHERE ns.type = $label AND ns.name = wanted AND e.relationship_name = 'memory_spaces'
        RETURN DISTINCT member.id
        """

        try:
            member_rows = await self.query(member_cypher, {"names": node_name, "label": label})
            member_ids = {r[0] for r in member_rows} if member_rows else set()
        except Exception as err:
            _log.warning(f"Member query failed: {err}")
            member_ids = set()

        if not member_ids:
            _log.warning("No memory space members found")
            return [], []

        # Filter target_ids to members only
        filtered_ids = [tid for tid in target_ids if tid in member_ids]

        if not filtered_ids:
            _log.debug("No target_ids in memory space")
            return [], []

        # Query edges in batches
        BATCH = 200
        all_rows = []

        for i in range(0, len(filtered_ids), BATCH):
            batch = filtered_ids[i : i + BATCH]

            edge_cypher = """
            MATCH (a:Node)-[r:EDGE]-(b:Node)
            WHERE a.id IN $ids AND r.relationship_name <> 'memory_spaces'
            RETURN DISTINCT
                a.id, a.name, a.type, a.properties, a.created_at, a.updated_at,
                b.id, b.name, b.type, b.properties, b.created_at, b.updated_at,
                r.relationship_name, r.properties
            """

            try:
                batch_rows = await self.query(edge_cypher, {"ids": batch})
                if batch_rows:
                    all_rows.extend(batch_rows)
            except Exception as err:
                _log.warning(f"Edge batch {i} failed: {err}")

        if not all_rows:
            return [], []

        # Process results
        nodes_map = {}
        edges = []

        for row in all_rows:
            (
                a_id,
                a_name,
                a_type,
                a_props,
                a_created_at,
                a_updated_at,
                b_id,
                b_name,
                b_type,
                b_props,
                b_created_at,
                b_updated_at,
                rel,
                rel_props,
            ) = row

            if a_id in member_ids and b_id in member_ids:
                a_data = {"id": a_id, "name": a_name, "type": a_type}
                a_data.update(_parse_props_json(a_props))
                if a_created_at is not None:
                    a_data["created_at"] = (
                        _datetime_to_ms(a_created_at) if isinstance(a_created_at, datetime) else a_created_at
                    )
                if a_updated_at is not None:
                    a_data["updated_at"] = (
                        _datetime_to_ms(a_updated_at) if isinstance(a_updated_at, datetime) else a_updated_at
                    )
                nodes_map[a_id] = (a_id, a_data)

                b_data = {"id": b_id, "name": b_name, "type": b_type}
                b_data.update(_parse_props_json(b_props))
                if b_created_at is not None:
                    b_data["created_at"] = (
                        _datetime_to_ms(b_created_at) if isinstance(b_created_at, datetime) else b_created_at
                    )
                if b_updated_at is not None:
                    b_data["updated_at"] = (
                        _datetime_to_ms(b_updated_at) if isinstance(b_updated_at, datetime) else b_updated_at
                    )
                nodes_map[b_id] = (b_id, b_data)

                edges.append((a_id, b_id, rel, _parse_props_json(rel_props)))

        return list(nodes_map.values()), edges

    # =========================================================================
    # Document Operations
    # =========================================================================

    async def get_document_subgraph(self, data_id: str):
        """Get document-related nodes for deletion."""
        doc_types = [
            "TextDocument",
            "PdfDocument",
            "AudioDocument",
            "ImageDocument",
            "UnstructuredDocument",
        ]
        type_filter = " OR ".join(f"doc.type = '{t}'" for t in doc_types)

        cypher = f"""
        MATCH (doc:Node)
        WHERE ({type_filter}) AND doc.id = $id
        
        OPTIONAL MATCH (doc)<-[e1:EDGE]-(chunk:Node)
        WHERE e1.relationship_name = 'is_part_of' AND chunk.type = 'ContentFragment'
        
        OPTIONAL MATCH (chunk)-[e2:EDGE]->(entity:Node)
        WHERE e2.relationship_name = 'contains' AND entity.type IN ['Entity', 'Entity']
        AND NOT EXISTS {{
            MATCH (entity)<-[e3:EDGE]-(other_chunk:Node)-[e4:EDGE]->(other_doc:Node)
            WHERE e3.relationship_name = 'contains' AND e4.relationship_name = 'is_part_of'
            AND ({type_filter.replace("doc.", "other_doc.")}) AND other_doc.id <> doc.id
        }}
        
        OPTIONAL MATCH (chunk)<-[e5:EDGE]-(digest:Node)
        WHERE e5.relationship_name = 'made_from' AND digest.type = 'FragmentDigest'
        
        OPTIONAL MATCH (entity)-[e6:EDGE]->(ctype:Node)
        WHERE e6.relationship_name = 'is_a' AND ctype.type IN ['EntityType', 'EntityType']
        AND NOT EXISTS {{
            MATCH (ctype)<-[e7:EDGE]-(other_concept:Node)-[e8:EDGE]-(other_chunk:Node)-[e9:EDGE]-(other_doc:Node)
            WHERE e7.relationship_name = 'is_a' AND e8.relationship_name = 'contains' AND e9.relationship_name = 'is_part_of'
            AND other_concept.type IN ['Entity', 'Entity'] AND other_chunk.type = 'ContentFragment'
            AND ({type_filter.replace("doc.", "other_doc.")}) AND other_doc.id <> doc.id
        }}
        
        RETURN
            COLLECT(DISTINCT doc) as document,
            COLLECT(DISTINCT chunk) as chunks,
            COLLECT(DISTINCT entity) as orphan_entities,
            COLLECT(DISTINCT digest) as made_from_nodes,
            COLLECT(DISTINCT ctype) as orphan_types
        """

        result = await self.query(cypher, {"id": data_id})

        if not result or not result[0]:
            return None

        return {
            "document": result[0][0],
            "chunks": result[0][1],
            "orphan_entities": result[0][2],
            "made_from_nodes": result[0][3],
            "orphan_types": result[0][4],
        }

    async def get_degree_one_nodes(self, node_type: str):
        """Get nodes with exactly one connection."""
        valid = ["Entity", "EntityType", "Entity", "EntityType"]
        if node_type not in valid:
            raise ValueError(f"node_type must be one of {valid}")

        cypher = f"""
        MATCH (n:Node) WHERE n.type = '{node_type}'
        WITH n, COUNT {{ MATCH (n)--() }} as degree
        WHERE degree = 1
        RETURN n
        """
        result = await self.query(cypher)
        return [r[0] for r in result] if result else []

    # =========================================================================
    # Graph Management
    # =========================================================================

    async def delete_graph(self) -> None:
        """Delete entire graph database."""
        try:
            # Checkpoint before deleting to ensure WAL is flushed
            # (in case delete is called after writes without explicit checkpoint)
            if self._conn:
                try:
                    self._conn.execute("CHECKPOINT;")
                except Exception:
                    pass  # Ignore checkpoint errors during deletion
                self._conn.close()
                self._conn = None
            if self._db:
                self._db.close()
                self._db = None

            parent = os.path.dirname(self._path)
            name = os.path.basename(self._path)
            storage = get_file_storage(parent)

            if await storage.is_file(name):
                await storage.remove(name)
                await storage.remove(f"{name}.lock")
            else:
                await storage.remove_all(name)

            _log.info(f"Deleted Kùzu database: {self._path}")

        except Exception as err:
            _log.error(f"delete_graph failed: {err}")
            raise

    async def get_graph_metrics(self, extended: bool = False) -> Dict[str, Any]:
        """Calculate graph statistics."""
        try:
            schema = await self.get_model_independent_graph_data()
            len(schema.get("node_labels", []))
            len(schema.get("relationship_types", []))

            # Basic counts
            node_count_result = await self.query("MATCH (n:Node) RETURN COUNT(n)")
            edge_count_result = await self.query("MATCH ()-[r:EDGE]->() RETURN COUNT(r)")

            actual_nodes = node_count_result[0][0] if node_count_result else 0
            actual_edges = edge_count_result[0][0] if edge_count_result else 0

            metrics = {
                "num_nodes": actual_nodes,
                "num_edges": actual_edges,
                "mean_degree": (2 * actual_edges / actual_nodes) if actual_nodes else None,
                "edge_density": (actual_edges / (actual_nodes * (actual_nodes - 1)) if actual_nodes > 1 else 0),
                "num_connected_components": await self._count_components(),
                "sizes_of_connected_components": await self._component_sizes(),
            }

            if extended:
                metrics.update(
                    {
                        "num_selfloops": await self._count_self_loops(),
                        "diameter": -1,
                        "avg_shortest_path_length": -1,
                        "avg_clustering": -1,
                    }
                )
            else:
                metrics.update(
                    {
                        "num_selfloops": -1,
                        "diameter": -1,
                        "avg_shortest_path_length": -1,
                        "avg_clustering": -1,
                    }
                )

            return metrics

        except Exception as err:
            _log.error(f"get_graph_metrics failed: {err}")
            return {
                "num_nodes": 0,
                "num_edges": 0,
                "mean_degree": 0,
                "edge_density": 0,
                "num_connected_components": 0,
                "sizes_of_connected_components": [],
                "num_selfloops": -1,
                "diameter": -1,
                "avg_shortest_path_length": -1,
                "avg_clustering": -1,
            }

    async def _count_components(self) -> int:
        """Count connected components."""
        cypher = """
        MATCH (n:Node)
        WITH n.id AS nid
        MATCH path = (n)-[:EDGE*1..3]-(m)
        WITH nid, COLLECT(DISTINCT m.id) AS connected
        WITH COLLECT(DISTINCT connected + [nid]) AS components
        RETURN SIZE(components)
        """
        result = await self.query(cypher)
        return result[0][0] if result else 0

    async def _component_sizes(self) -> List[int]:
        """Get sizes of connected components."""
        cypher = """
        MATCH (n:Node)
        WITH n.id AS nid
        MATCH path = (n)-[:EDGE*1..3]-(m)
        WITH nid, COLLECT(DISTINCT m.id) AS connected
        WITH COLLECT(DISTINCT connected + [nid]) AS components
        UNWIND components AS comp
        RETURN SIZE(comp)
        """
        result = await self.query(cypher)
        return [r[0] for r in result] if result else []

    async def _count_self_loops(self) -> int:
        """Count self-referential edges."""
        result = await self.query("MATCH (n:Node)-[r:EDGE]->(n) RETURN COUNT(r)")
        return result[0][0] if result else 0

    # =========================================================================
    # User Interaction
    # =========================================================================

    async def get_last_user_interaction_ids(self, limit: int) -> List[str]:
        """Get recent user interaction node IDs."""
        cypher = """
        MATCH (n) WHERE n.type = 'MflowUserInteraction'
        RETURN n.id as id
        ORDER BY n.created_at DESC
        LIMIT $max
        """
        rows = await self.query(cypher, {"max": limit})
        return [r[0] for r in rows]

    async def apply_feedback_weight(self, node_ids: List[str], weight: float) -> None:
        """Update feedback weight on answer edges."""
        # Fetch existing edges
        fetch_cypher = """
        MATCH (n:Node)-[r:EDGE]->()
        WHERE n.id IN $ids AND r.relationship_name = 'used_graph_element_to_answer'
        RETURN r.properties, n.id
        """
        results = await self.query(fetch_cypher, {"ids": node_ids})

        # Update weights client-side
        updates = []
        for props_raw, src_id in results:
            props = _parse_props_json(props_raw)
            props["feedback_weight"] = props.get("feedback_weight", 0) + weight
            updates.append((src_id, _dump_props(props)))

        # Write back
        for nid, new_props in updates:
            update_cypher = """
            MATCH (n:Node)-[r:EDGE]->()
            WHERE n.id = $nid AND r.relationship_name = 'used_graph_element_to_answer'
            SET r.properties = $props
            """
            await self.query(update_cypher, {"nid": nid, "props": new_props})

    async def get_triplets_batch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Get batch of triplets for export."""
        if offset < 0:
            raise ValueError(f"offset must be non-negative: {offset}")
        if limit < 0:
            raise ValueError(f"limit must be non-negative: {limit}")

        cypher = """
        MATCH (start:Node)-[rel:EDGE]->(end:Node)
        RETURN {
            start_node: {id: start.id, name: start.name, type: start.type, properties: start.properties},
            relationship_properties: {relationship_name: rel.relationship_name, properties: rel.properties},
            end_node: {id: end.id, name: end.name, type: end.type, properties: end.properties}
        } AS triplet
        SKIP $skip LIMIT $max
        """

        try:
            results = await self.query(cypher, {"skip": offset, "max": limit})
        except Exception as err:
            _log.error(f"Triplet query failed: {err}")
            raise

        triplets = []
        for idx, row in enumerate(results):
            try:
                if not row or not isinstance(row[0], dict):
                    continue

                triplet = row[0]

                # Parse start node
                if "start_node" in triplet and isinstance(triplet["start_node"], dict):
                    triplet["start_node"] = _merge_node_props(triplet["start_node"].copy())

                # Parse relationship
                if "relationship_properties" in triplet and isinstance(triplet["relationship_properties"], dict):
                    rel = triplet["relationship_properties"].copy()
                    rel_name = rel.get("relationship_name", "")
                    rel.update(_parse_props_json(rel.get("properties")))
                    if "properties" in rel:
                        del rel["properties"]
                    rel["relationship_name"] = rel_name
                    triplet["relationship_properties"] = rel

                # Parse end node
                if "end_node" in triplet and isinstance(triplet["end_node"], dict):
                    triplet["end_node"] = _merge_node_props(triplet["end_node"].copy())

                triplets.append(triplet)

            except Exception as err:
                _log.error(f"Triplet processing error at {idx}: {err}")

        return triplets

    # =========================================================================
    # Compatibility Properties
    # =========================================================================

    @property
    def KUZU_ASYNC_LOCK(self):
        return self._query_lock

    @property
    def open_connections(self):
        return self._active_conns

    @open_connections.setter
    def open_connections(self, value):
        self._active_conns = value

    @property
    def _is_closed(self):
        return self._closed

    @_is_closed.setter
    def _is_closed(self, value):
        self._closed = value
