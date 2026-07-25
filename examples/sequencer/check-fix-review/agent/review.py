"""Write the example review result without changing the target repository."""

import argparse
import json
from pathlib import Path


def main() -> None:
    """Report whether the fixed target has any remaining example finding."""
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("artifact_root", type=Path)
    arguments = parser.parse_args()

    passed = arguments.target.read_text() == "fixed\n"
    result = arguments.artifact_root / "review-result.json"
    result.write_text(json.dumps({"passed": passed}) + "\n")


if __name__ == "__main__":
    main()
