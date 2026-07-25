"""Apply the one repository mutation used by the sequencer example."""

import argparse
from pathlib import Path


def main() -> None:
    """Replace the broken target content."""
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    arguments = parser.parse_args()
    arguments.target.write_text("fixed\n")


if __name__ == "__main__":
    main()
