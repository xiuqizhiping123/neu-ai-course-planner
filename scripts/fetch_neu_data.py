"""Download raw Northeastern course data into a timestamped local snapshot.

Run from the repository root with uv run python scripts/fetch_neu_data.py.
Use --help for source selection and scope overrides.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from http.client import HTTPException
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import OpenerDirector, Request, build_opener, HTTPCookieProcessor

CATALOG = "https://catalog.northeastern.edu"
PROGRAM = (
    f"{CATALOG}/graduate/engineering/multidisciplinary/"
    "software-engineering-systems-ms/"
)
BANNER = "https://nubanner.neu.edu/StudentRegistrationSsb/ssb"
SEARCHNEU = "https://searchneu.com/catalog"
DEFAULT_TERM = "2027301"
BANNER_TERM_IDS = {"2027301": "202730"}
DEFAULT_BANNER_SUBJECTS = ("INFO", "CSYE", "DAMG")
DEFAULT_CATALOG_SUBJECTS = (*DEFAULT_BANNER_SUBJECTS, "ENCP", "TELE")
DEFAULT_COURSES = (
    "INFO 5100", "INFO 5101", "CSYE 6200", "CSYE 6225", "CSYE 7230", "INFO 6205",
)
SOURCES = ("catalog", "banner", "searchneu")
PAGE_SIZE = 100


class Snapshot:
    def __init__(self, directory: Path, term: str, sources: list[str]) -> None:
        self.directory = directory
        self.term = term
        self.sources = sources
        self.started_at = datetime.now(UTC).isoformat()
        self.files: list[dict[str, object]] = []
        self.errors: list[dict[str, str]] = []
        self.opener = build_opener()
        directory.mkdir(parents=True)

    def download(
        self,
        source: str,
        filename: str,
        url: str,
        *,
        opener: OpenerDirector | None = None,
        form: dict[str, str] | None = None,
    ) -> bytes:
        client = opener if opener is not None else self.opener
        request = Request(
            url,
            data=urlencode(form).encode("ascii") if form is not None else None,
            headers={"User-Agent": "NEU-Course-Planner-Snapshot/1.0"},
        )
        with client.open(request, timeout=25) as response:
            data = response.read()
            fetched_at = datetime.now(UTC).isoformat()
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type")
            status = response.status
        target = self.directory / filename
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        entry: dict[str, object] = {
            "source": source,
            "path": filename,
            "method": request.get_method(),
            "url": url,
            "final_url": final_url,
            "http_status": status,
            "content_type": content_type,
            "fetched_at": fetched_at,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if form is not None:
            entry["form"] = form
        self.files.append(entry)
        return data

    def record_error(self, source: str, target: str, error: Exception) -> None:
        detail = str(error)
        self.errors.append({"source": source, "target": target, "error": detail})
        print(f"{source} {target}: {detail}", file=sys.stderr)

    def write_manifest(self, *, complete: bool) -> None:
        manifest = {
            "term": self.term,
            "sources": self.sources,
            "started_at": self.started_at,
            "complete": complete,
            "files": self.files,
            "errors": self.errors,
        }
        payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        (self.directory / "manifest.json").write_text(
            payload, encoding="utf-8", newline="\n"
        )


def fetch_banner_subject(snapshot: Snapshot, subject: str) -> None:
    term = BANNER_TERM_IDS.get(snapshot.term, snapshot.term)
    # Banner remembers the query in its session, so each subject gets a fresh one.
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    start = f"{BANNER}/term/search?mode=search"
    snapshot.download(
        "banner",
        f"banner/{subject}-term.json",
        start,
        opener=opener,
        form={
            "term": term,
            "studyPath": "",
            "studyPathText": "",
            "startDatepicker": "",
            "endDatepicker": "",
        },
    )
    offset = 0
    expected_total: int | None = None
    seen_crns: set[str] = set()
    boston_crns: list[str] = []
    while expected_total is None or offset < expected_total:
        query = urlencode({
            "txt_subject": subject,
            "txt_term": term,
            "startDatepicker": "",
            "endDatepicker": "",
            "pageOffset": offset,
            "pageMaxSize": PAGE_SIZE,
            "sortColumn": "courseReferenceNumber",
            "sortDirection": "asc",
        })
        url = f"{BANNER}/searchResults/searchResults?{query}"
        raw = snapshot.download(
            "banner", f"banner/{subject}-{offset:05d}.json", url, opener=opener
        )
        page = json.loads(raw)
        if not isinstance(page, dict) or page.get("success") is not True:
            raise ValueError(f"Banner search failed at offset {offset}: {url}")
        total = page.get("totalCount")
        rows = page.get("data")
        if offset == 0 and type(total) is int and total == 0 and rows in (None, []):
            raise ValueError(f"Banner returned zero sections for {subject}, term {term}")
        if type(total) is not int or total < 0 or not isinstance(rows, list):
            raise ValueError(f"Unexpected Banner response at offset {offset}: {url}")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(f"Banner result count changed during pagination: {url}")
        if (not rows and offset < total) or offset + len(rows) > total:
            raise ValueError(f"Unexpected number of Banner results: {url}")
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError(f"Unexpected Banner row: {url}")
            crn = str(row.get("courseReferenceNumber", ""))
            if (
                row.get("subject") != subject
                or str(row.get("term")) != term
                or not crn.isdigit()
                or crn in seen_crns
            ):
                raise ValueError(f"Unexpected or repeated Banner section {crn}: {url}")
            seen_crns.add(crn)
            if row.get("campusDescription") == "Boston":
                boston_crns.append(crn)
        offset += len(rows)

    for crn in boston_crns:
        query = urlencode({"term": term, "courseReferenceNumber": crn})
        url = f"{BANNER}/searchResults/getFacultyMeetingTimes?{query}"
        try:
            raw = snapshot.download(
                "banner", f"banner/faculty-{term}-{crn}.json", url, opener=opener
            )
            detail = json.loads(raw)
            if not isinstance(detail, dict) or not isinstance(detail.get("fmt"), list):
                raise TypeError(f"Unexpected Banner faculty response: {url}")
        except (OSError, HTTPException, TypeError, ValueError) as error:
            snapshot.record_error("banner", crn, error)


def fetch_html(snapshot: Snapshot, source: str, filename: str, url: str) -> None:
    try:
        snapshot.download(source, filename, url)
    except (OSError, HTTPException, ValueError) as error:
        snapshot.record_error(source, url, error)


def subject_code(value: str) -> str:
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,6}", code):
        raise argparse.ArgumentTypeError(f"Invalid subject code: {value}")
    return code


def course_code(value: str) -> str:
    code = " ".join(value.upper().split())
    if not re.fullmatch(r"[A-Z]{2,6} [0-9]{4}[A-Z]?", code):
        raise argparse.ArgumentTypeError(f"Invalid course code: {value}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--term",
        default=DEFAULT_TERM,
        help="Term ID (default: SearchNEU 2027301, mapped to Banner 202730).",
    )
    parser.add_argument(
        "--source", action="append", choices=SOURCES,
        help="Select a source, repeatable. Defaults to all sources.",
    )
    parser.add_argument(
        "--banner-subject", action="append", type=subject_code,
        help="Override Banner subjects (default INFO CSYE DAMG), repeatable.",
    )
    parser.add_argument(
        "--catalog-subject", action="append", type=subject_code,
        help="Override Catalog subjects (default INFO CSYE DAMG ENCP TELE), repeatable.",
    )
    parser.add_argument(
        "--searchneu-course", action="append", type=course_code,
        help="Override SearchNEU sample courses, repeatable (for example 'INFO 5100').",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{6,7}", args.term):
        parser.error("--term must contain six or seven digits")
    sources: list[str] = list(dict.fromkeys(args.source or SOURCES))
    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    snapshot = Snapshot(args.output_dir / snapshot_id, args.term, sources)
    print(f"Writing raw snapshot to {snapshot.directory}", flush=True)

    complete = False
    try:
        if "catalog" in sources:
            catalog_subjects = args.catalog_subject or DEFAULT_CATALOG_SUBJECTS
            for subject in dict.fromkeys(catalog_subjects):
                slug = subject.lower()
                url = f"{CATALOG}/course-descriptions/{slug}/"
                fetch_html(snapshot, "catalog", f"catalog/{slug}.html", url)
            fetch_html(snapshot, "catalog", "catalog/ms-ses-boston.html", PROGRAM)
        if "banner" in sources:
            for subject in dict.fromkeys(args.banner_subject or DEFAULT_BANNER_SUBJECTS):
                try:
                    fetch_banner_subject(snapshot, subject)
                except (OSError, HTTPException, TypeError, ValueError) as error:
                    snapshot.record_error("banner", subject, error)
        if "searchneu" in sources:
            for code in dict.fromkeys(args.searchneu_course or DEFAULT_COURSES):
                url = f"{SEARCHNEU}/{args.term}/{quote(code)}"
                filename = f"searchneu/{code.replace(' ', '-')}.html"
                fetch_html(snapshot, "searchneu", filename, url)
        complete = not snapshot.errors
    except KeyboardInterrupt:
        snapshot.record_error("snapshot", "all", ValueError("Interrupted"))
        return 130
    finally:
        snapshot.write_manifest(complete=complete)

    print(f"Saved {len(snapshot.files)} files; {len(snapshot.errors)} errors.")
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
