"""Write the example check result without changing the target repository."""

import argparse
import json
from pathlib import Path


def main() -> None:
    """Report whether the target contains the expected fixed content."""
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("artifact_root", type=Path)
    arguments = parser.parse_args()

    passed = arguments.target.read_text() == "fixed\n"
    result = arguments.artifact_root / "check-result.json"
    result.write_text(json.dumps({"passed": passed}) + "\n")


if __name__ == "__main__":
    main()
