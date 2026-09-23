#!/usr/bin/env python3
"""Run SplitScope from a source checkout: python SplitScope.py [file]"""
import sys

from splitscope.app import main

if __name__ == "__main__":
    sys.exit(main())
