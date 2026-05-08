"""
CLI Edge Cases and Error Scenarios
==================================
m_flow.tests.cli_tests.cli_unit_tests.test_cli_edge_cases

Comprehensive tests for CLI command edge cases including:
- Error handling and exception propagation
- Invalid input validation
- Unicode and special character support
- Mock-based execution testing
"""

import pytest
import sys
import asyncio
import argparse
from unittest.mock import patch, MagicMock, AsyncMock

from m_flow.cli.commands.add_command import AddCommand
from m_flow.cli.commands.search_command import SearchCommand
from m_flow.cli.commands.memorize_command import MemorizeCommand
from m_flow.cli.commands.delete_command import DeleteCommand
from m_flow.cli.commands.config_command import ConfigCommand
from m_flow.cli.exceptions import CliCommandException
from m_flow.data.methods.get_deletion_counts import DeletionCountsPreview
from m_flow.shared.enums.content_type import ContentType


def _execute_coroutine(coro):
    """Helper to execute coroutines in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAddEdgeCases:
    """Edge case tests for the add command."""

    @patch("m_flow.cli.commands.add_command.asyncio.run", side_effect=_execute_coroutine)
    def test_empty_data_input(self, mock_run):
        """Verifies add command handles empty data lists correctly."""
        mock_module = MagicMock()
        mock_module.add = AsyncMock()

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = AddCommand()
            cmd_args = argparse.Namespace(data=[], dataset_name="test_ds")
            cmd.execute(cmd_args)

        mock_run.assert_called_once()
        assert asyncio.iscoroutine(mock_run.call_args[0][0])
        mock_module.add.assert_awaited_once_with(data=[], dataset_name="test_ds")

    @patch("m_flow.cli.commands.add_command.asyncio.run")
    def test_event_loop_failure(self, mock_run):
        """Verifies proper exception handling when event loop fails."""
        cmd = AddCommand()
        cmd_args = argparse.Namespace(data=["file.txt"], dataset_name="ds")
        mock_run.side_effect = RuntimeError("Loop initialization failed")

        with pytest.raises(CliCommandException):
            cmd.execute(cmd_args)

    def test_special_path_characters(self):
        """Validates parsing of file paths with special characters."""
        cmd = AddCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)

        test_paths = [
            "path with spaces.txt",
            "path-with-dashes.txt",
            "path_with_underscores.txt",
            "path.multiple.dots.txt",
        ]

        parsed = parser.parse_args([*test_paths, "--dataset-name", "special_ds"])
        assert parsed.data == test_paths
        assert parsed.dataset_name == "special_ds"


class TestSearchEdgeCases:
    """Edge case tests for the search command."""

    @patch("m_flow.cli.commands.search_command.asyncio.run", side_effect=_execute_coroutine)
    def test_no_results_scenario(self, mock_run):
        """Tests graceful handling of empty search results."""
        mock_module = MagicMock()
        mock_module.search = AsyncMock(return_value=[])

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = SearchCommand()
            cmd_args = argparse.Namespace(
                query_text="nonexistent content",
                query_type="TRIPLET_COMPLETION",
                datasets=None,
                top_k=10,
                system_prompt=None,
                output_format="pretty",
            )
            cmd.execute(cmd_args)

        mock_run.assert_called_once()
        assert asyncio.iscoroutine(mock_run.call_args[0][0])
        mock_module.search.assert_awaited_once()

    @patch("m_flow.cli.commands.search_command.asyncio.run", side_effect=_execute_coroutine)
    def test_extreme_top_k_value(self, mock_run):
        """Tests search with unusually large top_k parameter."""
        mock_module = MagicMock()
        mock_module.search = AsyncMock(return_value=["single_result"])
        mock_run.return_value = ["single_result"]

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = SearchCommand()
            cmd_args = argparse.Namespace(
                query_text="test",
                query_type="EPISODIC",
                datasets=None,
                top_k=999999,
                system_prompt=None,
                output_format="json",
            )
            cmd.execute(cmd_args)

        mock_run.assert_called_once()
        called_kwargs = mock_module.search.await_args.kwargs
        assert called_kwargs["top_k"] == 999999

    @patch("builtins.__import__")
    def test_invalid_recall_mode(self, mock_import):
        """Tests error handling for invalid RecallMode enum values."""
        mock_recall = MagicMock()
        mock_recall.__getitem__.side_effect = KeyError("INVALID")

        def import_handler(name, fromlist=None, *args, **kwargs):
            if name == "m_flow.search.types":
                mod = MagicMock()
                mod.RecallMode = mock_recall
                return mod
            return MagicMock()

        mock_import.side_effect = import_handler

        cmd = SearchCommand()
        cmd_args = argparse.Namespace(
            query_text="test",
            query_type="INVALID",
            datasets=None,
            top_k=10,
            system_prompt=None,
            output_format="pretty",
        )

        with pytest.raises(CliCommandException):
            cmd.execute(cmd_args)

    def test_unicode_query_text(self):
        """Validates unicode character support in search queries."""
        cmd = SearchCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)

        unicode_text = "测试搜索 🔍 Ümläuts and spëcîäl"
        parsed = parser.parse_args([unicode_text])
        assert parsed.query_text == unicode_text

    @patch("m_flow.cli.commands.search_command.asyncio.run", side_effect=_execute_coroutine)
    def test_results_containing_none(self, mock_run):
        """Tests handling of None values in search results."""
        mock_module = MagicMock()
        mock_module.search = AsyncMock(return_value=[None, "valid", None])

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = SearchCommand()
            cmd_args = argparse.Namespace(
                query_text="test",
                query_type="EPISODIC",
                datasets=None,
                top_k=10,
                system_prompt=None,
                output_format="pretty",
            )
            cmd.execute(cmd_args)

        mock_module.search.assert_awaited_once()


class TestMemorizeEdgeCases:
    """Edge case tests for the memorize command."""

    @patch("m_flow.cli.commands.memorize_command.asyncio.run", side_effect=_execute_coroutine)
    def test_negative_chunk_size(self, mock_run):
        """Tests memorize with invalid negative chunk size."""
        mock_module = MagicMock()
        mock_module.memorize = AsyncMock()

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = MemorizeCommand()
            cmd_args = argparse.Namespace(
                datasets=None,
                chunk_size=-100,
                chunker="TextChunker",
                background=False,
                verbose=False,
                content_type="text",
            )
            cmd.execute(cmd_args)

        mock_run.assert_called_once()
        from m_flow.ingestion.chunking.TextChunker import TextChunker

        mock_module.memorize.assert_awaited_once_with(
            datasets=None,
            chunk_size=-100,
            chunker=TextChunker,
            run_in_background=False,
            content_type=ContentType.TEXT,
        )

    @patch("m_flow.cli.commands.memorize_command.asyncio.run")
    def test_chunker_import_fallback(self, mock_run):
        """Tests fallback when LangchainChunker import fails."""
        mock_module = MagicMock()
        mock_module.memorize = AsyncMock()

        def import_handler(name, fromlist=None, *args, **kwargs):
            if name == "m_flow":
                return mock_module
            elif "LangchainChunker" in str(fromlist):
                raise ImportError("LangchainChunker unavailable")
            elif "TextChunker" in str(fromlist):
                mod = MagicMock()
                mod.TextChunker = MagicMock()
                return mod
            return MagicMock()

        with (
            patch("builtins.__import__", side_effect=import_handler),
            patch.dict(sys.modules, {"m_flow": mock_module}),
        ):
            cmd = MemorizeCommand()
            cmd_args = argparse.Namespace(
                datasets=None,
                chunk_size=None,
                chunker="LangchainChunker",
                background=False,
                verbose=True,
                content_type="text",
            )
            cmd.execute(cmd_args)

        mock_run.assert_called_once()

    @patch("m_flow.cli.commands.memorize_command.asyncio.run", side_effect=_execute_coroutine)
    def test_empty_dataset_list_normalization(self, mock_run):
        """Tests that empty dataset lists are normalized to None."""
        mock_module = MagicMock()
        mock_module.memorize = AsyncMock()

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = MemorizeCommand()
            cmd_args = argparse.Namespace(
                datasets=[],
                chunk_size=None,
                chunker="TextChunker",
                background=False,
                verbose=False,
                content_type="text",
            )
            cmd.execute(cmd_args)

        from m_flow.ingestion.chunking.TextChunker import TextChunker

        mock_module.memorize.assert_awaited_once_with(
            datasets=None,
            chunk_size=None,
            chunker=TextChunker,
            run_in_background=False,
            content_type=ContentType.TEXT,
        )


class TestDeleteEdgeCases:
    """Edge case tests for the delete command."""

    @patch("m_flow.cli.commands.delete_command.get_deletion_counts")
    @patch("m_flow.cli.commands.delete_command.output.confirm")
    @patch("m_flow.cli.commands.delete_command.asyncio.run", side_effect=_execute_coroutine)
    def test_all_flag_with_user_id(self, mock_run, mock_confirm, mock_counts):
        """Tests delete command behavior with both --all and --user-id flags."""
        mock_module = MagicMock()
        mock_module.remove = AsyncMock()
        mock_counts.return_value = DeletionCountsPreview()

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = DeleteCommand()
            cmd_args = argparse.Namespace(
                dataset_name=None,
                user_id="target_user",
                all=True,
                force=False,
            )
            mock_confirm.return_value = True
            cmd.execute(cmd_args)

        mock_confirm.assert_called_once_with("Purge ALL M-flow data?")
        mock_module.remove.assert_awaited_once_with(
            dataset_name=None,
            user_id="target_user",
        )

    @patch("m_flow.cli.commands.delete_command.get_deletion_counts")
    @patch("m_flow.cli.commands.delete_command.output.confirm")
    def test_keyboard_interrupt_during_confirm(self, mock_confirm, mock_counts):
        """Tests graceful handling of keyboard interrupt during confirmation."""
        mock_counts.return_value = DeletionCountsPreview()

        cmd = DeleteCommand()
        cmd_args = argparse.Namespace(
            dataset_name="test_ds",
            user_id=None,
            all=False,
            force=False,
        )
        mock_confirm.side_effect = KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            cmd.execute(cmd_args)

    @patch("m_flow.cli.commands.delete_command.asyncio.run")
    def test_database_connection_failure(self, mock_run):
        """Tests exception propagation on database errors."""
        cmd = DeleteCommand()
        cmd_args = argparse.Namespace(
            dataset_name="test_ds",
            user_id=None,
            all=False,
            force=True,
        )
        mock_run.side_effect = ValueError("Connection refused")

        with pytest.raises(CliCommandException):
            cmd.execute(cmd_args)

    def test_special_dataset_names(self):
        """Tests parser handling of special characters in dataset names."""
        cmd = DeleteCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)

        special_cases = [
            "dataset with spaces",
            "dataset-dashes",
            "dataset_underscores",
            "dataset.dots",
            "path/slashes",
        ]
        for name in special_cases:
            parsed = parser.parse_args(["--dataset-name", name])
            assert parsed.dataset_name == name


class TestConfigEdgeCases:
    """Edge case tests for the config command."""

    def test_missing_subcommand(self):
        """Tests config command with no subcommand specified."""
        cmd = ConfigCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)

        parsed = parser.parse_args([])
        assert not hasattr(parsed, "action") or parsed.action is None

    @patch("builtins.__import__")
    def test_get_nonexistent_key(self, mock_import):
        """Tests config get behavior for missing keys."""
        mock_module = MagicMock()
        mock_module.config.get = MagicMock(side_effect=KeyError("unknown"))
        mock_import.return_value = mock_module

        cmd = ConfigCommand()
        cmd_args = argparse.Namespace(action="get", key="unknown_key")
        cmd.execute(cmd_args)

        mock_module.config.get.assert_called_once_with("unknown_key")

    @patch("builtins.__import__")
    def test_set_nested_json_value(self, mock_import):
        """Tests config set with complex nested JSON values."""
        mock_module = MagicMock()
        mock_module.config.set = MagicMock()
        mock_import.return_value = mock_module

        cmd = ConfigCommand()
        json_val = '{"nested": {"inner": "value"}, "list": [1, 2, 3]}'
        expected_parsed = {"nested": {"inner": "value"}, "list": [1, 2, 3]}
        cmd_args = argparse.Namespace(
            action="set",
            key="complex_key",
            value=json_val,
        )

        cmd.execute(cmd_args)
        mock_module.config.set.assert_called_once_with("complex_key", expected_parsed)

    @patch("builtins.__import__")
    def test_set_malformed_json_as_string(self, mock_import):
        """Tests that malformed JSON is passed as raw string."""
        mock_module = MagicMock()
        mock_module.config.set = MagicMock()
        mock_import.return_value = mock_module

        cmd = ConfigCommand()
        bad_json = '{"broken": json}'
        cmd_args = argparse.Namespace(action="set", key="key", value=bad_json)

        cmd.execute(cmd_args)
        mock_module.config.set.assert_called_once_with("key", bad_json)

    @patch("m_flow.cli.commands.config_command.output.confirm")
    def test_unset_with_confirmation(self, mock_confirm):
        """Tests config unset with user confirmation flow."""
        mock_module = MagicMock()

        with patch.dict(sys.modules, {"m_flow": mock_module}):
            cmd = ConfigCommand()
            cmd_args = argparse.Namespace(
                action="unset",
                key="some_key",
                force=False,
            )
            mock_confirm.return_value = True
            cmd.execute(cmd_args)

        mock_confirm.assert_called_once()

    @patch("builtins.__import__")
    def test_unset_missing_method(self, mock_import):
        """Tests unset when expected method doesn't exist on config."""
        mock_module = MagicMock()
        mock_module.config = MagicMock()
        mock_import.return_value = mock_module

        cmd = ConfigCommand()
        cmd_args = argparse.Namespace(
            action="unset",
            key="provider",
            force=True,
        )
        cmd.execute(cmd_args)

        mock_module.config.unset.assert_not_called()

    def test_unknown_subcommand_handling(self):
        """Tests graceful handling of unknown config subcommands."""
        cmd = ConfigCommand()
        cmd_args = argparse.Namespace(action="unknown_action")
        cmd.execute(cmd_args)  # Should not raise


class TestGeneralCommandBehavior:
    """Cross-cutting edge case tests for all commands."""

    def test_null_args_handling(self):
        """Tests command behavior when args is None."""
        all_commands = [
            AddCommand(),
            SearchCommand(),
            MemorizeCommand(),
            DeleteCommand(),
            ConfigCommand(),
        ]
        for cmd in all_commands:
            try:
                cmd.execute(None)
            except (AttributeError, CliCommandException):
                pass  # Expected for None args

    def test_null_parser_handling(self):
        """Tests parser configuration with None parser."""
        all_commands = [
            AddCommand(),
            SearchCommand(),
            MemorizeCommand(),
            DeleteCommand(),
            ConfigCommand(),
        ]
        for cmd in all_commands:
            try:
                cmd.configure_parser(None)
            except AttributeError:
                pass  # Expected for None parser

    def test_command_metadata_types(self):
        """Validates command property types are correct strings."""
        all_commands = [
            AddCommand(),
            SearchCommand(),
            MemorizeCommand(),
            DeleteCommand(),
            ConfigCommand(),
        ]
        for cmd in all_commands:
            assert isinstance(cmd.command_string, str) and len(cmd.command_string) > 0
            assert isinstance(cmd.help_string, str) and len(cmd.help_string) > 0

            if hasattr(cmd, "description") and cmd.description:
                assert isinstance(cmd.description, str)
            if hasattr(cmd, "docs_url") and cmd.docs_url:
                assert isinstance(cmd.docs_url, str)

    @patch("tempfile.NamedTemporaryFile")
    def test_temp_file_path_parsing(self, mock_temp):
        """Tests add command with temporary file paths."""
        mock_file = MagicMock()
        mock_file.name = "/tmp/mflow_test_file.txt"
        mock_temp.return_value.__enter__.return_value = mock_file

        cmd = AddCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)

        parsed = parser.parse_args([mock_file.name])
        assert parsed.data == [mock_file.name]
