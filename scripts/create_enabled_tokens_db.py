#!/usr/bin/env python3
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import enabled_tokens_store


def main() -> None:
    enabled_tokens_store.init_db()
    print(f"enabled_tokens database initialized at: {enabled_tokens_store.db_path()}")


if __name__ == "__main__":
    main()
