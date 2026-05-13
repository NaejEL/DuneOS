#!/usr/bin/env python3
"""Entry point — delegates to the dbt package."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dbt.cli import main

if __name__ == "__main__":
    main()
