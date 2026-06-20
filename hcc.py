#!/usr/bin/env python3
"""Compatibility entrypoint for the HCC command-line interface."""

from hcc.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
