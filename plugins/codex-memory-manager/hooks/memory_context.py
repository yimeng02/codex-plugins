#!/usr/bin/env python3
"""Codex command-hook entry point; independent of MCP startup readiness."""

from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_manager.autoload import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare Codex global memory context")
    parser.add_argument("--memory-home", type=Path)
    main(memory_home=parser.parse_args().memory_home)
