#!/usr/bin/env python3
# m_flow/debug/__main__.py
"""
P6-4: Debug Module Entry Point

Usage:
    python -m m_flow.debug trace --trace_id <id>
    python -m m_flow.debug procedure --procedure_id <id>
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m m_flow.debug trace --trace_id <id>")
        print("  python -m m_flow.debug trace --list")
        print("  python -m m_flow.debug procedure --procedure_id <id>")
        print("  python -m m_flow.debug procedure --list")
        return

    cmd = sys.argv[1]
    sys.argv = [sys.argv[0], *sys.argv[2:]]  # Remove subcommand

    if cmd == "trace":
        from m_flow.debug.trace_cli import main as trace_main

        trace_main()
    elif cmd == "procedure":
        from m_flow.debug.procedure_cli import main as procedure_main

        procedure_main()
    else:
        print(f"Unknown command: {cmd}")
        print("Available commands: trace, procedure")


if __name__ == "__main__":
    main()
