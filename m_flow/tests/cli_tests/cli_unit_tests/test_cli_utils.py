"""
CLI工具函数测试
"""

from __future__ import annotations

import inspect

from m_flow.cli import debug
from m_flow.cli.app import _load_commands as _discover_commands
from m_flow.cli.config import (
    CHUNKER_CHOICES,
    CLI_DESCRIPTION,
    COMMAND_DESCRIPTIONS,
    DEFAULT_DOCS_URL,
    OUTPUT_FORMAT_CHOICES,
    RECALL_MODE_CHOICES,
)


class TestCliConfig:
    """CLI配置测试"""

    def test_description(self):
        """测试CLI描述"""
        assert CLI_DESCRIPTION
        assert "m-flow" in CLI_DESCRIPTION.lower()

    def test_docs_url(self):
        """测试文档URL"""
        assert DEFAULT_DOCS_URL.startswith("https://")
        assert "FlowElement-xinliuyuansu" in DEFAULT_DOCS_URL

    def test_command_descriptions(self):
        """测试命令描述完整性"""
        cmds = [c().command_string for c in _discover_commands()]
        assert len(cmds) > 0
        for cmd in cmds:
            assert cmd in COMMAND_DESCRIPTIONS
            assert len(COMMAND_DESCRIPTIONS[cmd]) > 0

    def test_recall_modes(self):
        """测试召回模式"""
        assert isinstance(RECALL_MODE_CHOICES, list)
        for mode in ("TRIPLET_COMPLETION", "CYPHER", "EPISODIC", "PROCEDURAL", "CHUNKS_LEXICAL"):
            assert mode in RECALL_MODE_CHOICES

    def test_chunker_choices(self):
        """测试分块器选项"""
        assert "TextChunker" in CHUNKER_CHOICES
        assert "LangchainChunker" in CHUNKER_CHOICES

    def test_output_formats(self):
        """测试输出格式"""
        for fmt in ("json", "pretty", "simple"):
            assert fmt in OUTPUT_FORMAT_CHOICES


class TestCliProtocol:
    """CLI协议测试"""

    def test_protocol_annotations(self):
        """测试协议注解"""
        from m_flow.cli.reference import SupportsCliCommand

        annots = SupportsCliCommand.__annotations__
        for attr in ("command_string", "help_string", "description", "docs_url"):
            assert attr in annots

    def test_protocol_methods(self):
        """测试协议方法"""
        from m_flow.cli.reference import SupportsCliCommand

        methods = [n for n, _ in inspect.getmembers(SupportsCliCommand)]
        assert "configure_parser" in methods
        assert "execute" in methods


class TestDebugUtils:
    """调试工具测试"""

    def test_multiple_enable_calls(self):
        """测试多次启用调试"""
        debug.enable_debug()
        debug.enable_debug()
        assert debug.is_debug_enabled()
        debug._debug_enabled = False
