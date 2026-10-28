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
        python_version = data["tool"]["poetry"]["dependencies"]["python"]
    except KeyError:
        sys.exit("`python not found in Poetry dependencies.")

    if not isinstance(python_version, str) or not is_valid_version(python_version):
        sys.exit(f"`nautobot` must be an exact version, got: {python_version!r}")

    print(python_version)
