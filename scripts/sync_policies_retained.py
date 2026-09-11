#!/usr/bin/env python3
"""Backward-compatible entry point for the retained policy collector."""
from sync_policies import main

if __name__ == "__main__":
    raise SystemExit(main())
