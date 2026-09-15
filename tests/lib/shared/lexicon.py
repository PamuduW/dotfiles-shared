"""The presentation contract's lexicon rule, checked over one repository.

Both products print to the same operator, so they use one vocabulary:

  Update   the repository/config/skills/tools operation, in both products
  Upgrade  reserved for apt package upgrades, where it is the OS's own word
  Headers  sentence case -- proper nouns stay capitalised, nothing else does

Usage: lexicon.py <repo-root> <glob> [<glob> ...]

Prints one line per violation and exits non-zero. Silence means the tree
agrees with the contract.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Capitalised anywhere in a header: product names, proper nouns, initialisms.
#: Anything else capitalised after the first word is Title Case, which this
#: contract does not use.
PROPER = {
    "Agentbot",
    "Dotfiles",
    "Graphify",
    "Boost",
    "Cursor",
    "Claude",
    "Codex",
    "GitHub",
    "GitLab",
    "MCP",
    "VS",
    "Code",
    "WSL",
    "Python",
    "Copilot",
    "Docker",
    "Portainer",
    "Go",
    "Stow",
}

#: Spellings the update/upgrade split has already been fixed in. They are
#: pinned by name because each one shipped saying "upgrade" for an operation
#: that is not apt's.
RETIRED = ("Upgrade summary", "Upgrade finished", "verified upgrade", "=== Upgrade ===")

# Any header-printing call, not just print_header: a product wrapper named
# something else -- _package_lib_header, for one -- prints the same surface and
# was invisible to this check while it matched two spellings. Calls whose first
# argument is built at runtime are skipped below, which is what keeps the
# four-column header helpers (whose first argument is a width) out of it.
HEADER = re.compile(r"""[A-Za-z_][A-Za-z0-9_]*_header\(?\s*(['"])([^'"]+)\1""")


def _files(root: Path, globs: list[str]):
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


def check(root: Path, globs: list[str]) -> list[str]:
    failures: list[str] = []
    for path in _files(root, globs):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root)

        for match in HEADER.finditer(text):
            title = match.group(2)
            if "$" in title or "{" in title:
                continue  # built at runtime; the parts are checked where they are literal
            for word in title.split()[1:]:
                bare = word.strip("()[],.:")
                if bare[:1].isupper() and bare not in PROPER:
                    failures.append(f"{rel}: Title Case header {title!r} (word {bare!r})")
            if re.search(r"\bUpgrad", title):
                failures.append(f"{rel}: header {title!r} says Upgrade; the operation is Update")

        for retired in RETIRED:
            if retired in text:
                failures.append(f"{rel}: {retired!r} is retired; the operation is Update")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    failures = check(Path(argv[1]).resolve(), argv[2:])
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
