from pathlib import Path
import re
import sys
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "wiki"


def check_source_attribution(pages, all_names):
    failures = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if "Source" not in text and "Source file" not in text and "Source contribution" not in text:
            failures.append(str(page.relative_to(ROOT)))
    return {
        "name": "Source attribution",
        "description": "all pages cite their source" if not failures else f"{len(failures)} pages missing source citation",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_page_length(pages, all_names):
    failures = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        char_count = len(text.strip())
        if char_count < 200:
            failures.append(f"{page.relative_to(ROOT)} ({char_count} chars)")
    return {
        "name": "Page length",
        "description": "all pages meet minimum length" if not failures else f"{len(failures)} pages below minimum threshold",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_internal_links(pages, all_names):
    failures = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        links = re.findall(r"\[\[([^\]]+)\]\]", text)
        if not links:
            failures.append(str(page.relative_to(ROOT)))
    return {
        "name": "Internal links",
        "description": "all pages have at least one internal link" if not failures else f"{len(failures)} pages with no internal links",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_orphaned_links(pages, all_names):
    failures = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        links = re.findall(r"\[\[([^\]]+)\]\]", text)
        if links:
            resolved = sum(1 for l in links if l in all_names)
            if resolved == 0:
                unresolved = [l for l in links if l not in all_names]
                failures.append(f"{page.relative_to(ROOT)}: {', '.join('[[' + l + ']]' for l in unresolved)}")
    return {
        "name": "Orphaned links",
        "description": "all links resolve to existing pages" if not failures else f"{len(failures)} pages with only unresolved links",
        "passed": len(failures) == 0,
        "details": failures,
    }


def main() -> int:
    pages = list(WIKI_DIR.rglob("*.md"))
    if not pages:
        print("No wiki pages found.")
        return 0

    all_names = {p.stem for p in pages}
    checks = [
        check_source_attribution(pages, all_names),
        check_page_length(pages, all_names),
        check_internal_links(pages, all_names),
        check_orphaned_links(pages, all_names),
    ]

    for check in checks:
        if check["passed"]:
            print(f"[PASS] {check['name']} -- {check['description']}")
        else:
            print(f"[FAIL] {check['name']} -- {check['description']}")
            for detail in check["details"]:
                print(f"       - {detail}")

    passed = sum(1 for c in checks if c["passed"])
    failed = len(checks) - passed
    print(f"\n{failed} checks failed, {passed} checks passed")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
