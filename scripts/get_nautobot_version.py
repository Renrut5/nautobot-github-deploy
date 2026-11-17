import re
import sys
import tomllib


def is_valid_version(input: str):
    """Check if string is valid, and definitive version."""
    return re.fullmatch(r"\d+\.\d+(\.\d+)?", input)

if __name__ == "__main__":
    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    try:
        nautobot_version = data["tool"]["poetry"]["dependencies"]["nautobot"]
    except KeyError:
        sys.exit("`nautobot` not found in Poetry dependencies.")

    if not isinstance(nautobot_version, str) or not is_valid_version(nautobot_version):
        sys.exit(f"`nautobot` must be an exact version, got: {nautobot_version!r}")

    print(nautobot_version)
