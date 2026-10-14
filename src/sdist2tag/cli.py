"""Command line interface for sdist2tag."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Tuple

from sdist2tag.core import (
    AboutVersionIndex,
    RULE_ID,
    CompareCache,
    GitError,
    SdistError,
    _get_sdist,
    compare,
    create_tags,
    index_about_versions,
    literal_about_version,
    pypi_project_files,
    read_project_name,
    read_version_path,
    resolve_history,
    sdist_versions,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdist2tag")
    commands = parser.add_subparsers(dest="command", required=True)
    investigate = commands.add_parser("investigate", help="compare PyPI sdists with Git history")
    source = investigate.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo-github", metavar="URL")
    source.add_argument("--repo-local", metavar="PATH", type=Path)
    investigate.add_argument("--ref", default="main")
    investigate.add_argument("--version")
    investigate.add_argument("--all", action="store_true", help="explicitly select all releases (the default)")
    investigate.add_argument("--sdist-pypi", metavar="URL")
    investigate.add_argument("--sdist-local", metavar="PATH", type=Path)
    investigate.add_argument("--report", type=Path, default=Path("sdist2tag-report.json"))
    investigate.add_argument("--archive-dir", type=Path)

    tag = commands.add_parser("tag", help="create local annotated tags from an investigation report")
    tag.add_argument("--repo-local", required=True, metavar="PATH", type=Path)
    tag.add_argument("--report", type=Path, default=Path("sdist2tag-report.json"))
    tag.add_argument("--tag-prefix", default="v")
    return parser


def _validate_github_url(url: str) -> None:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.strip("/"):
        raise SdistError("--repo-github must be an HTTPS github.com repository URL")


@contextmanager
def _repository(args: argparse.Namespace) -> Iterator[Tuple[Path, str]]:
    if args.repo_local is not None:
        repo = args.repo_local.resolve()
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise GitError("local repository path is not a Git repository")
        root = Path(result.stdout.decode().strip()).resolve()
        if root != repo:
            raise GitError("--repo-local must point to the repository root")
        yield repo, str(repo)
        return
    url = args.repo_github
    _validate_github_url(url)
    with tempfile.TemporaryDirectory(prefix="sdist2tag-git-") as temp:
        repo = Path(temp) / "repository.git"
        _progress("Cloning repository from GitHub (full history)...")
        result = subprocess.run(["git", "clone", "--bare", "--progress", "--", url, str(repo)])
        if result.returncode:
            raise GitError("could not clone GitHub repository (git clone exited with status {})".format(result.returncode))
        yield repo, url


def _investigate(args: argparse.Namespace) -> int:
    if args.all and args.version is not None:
        raise SdistError("--all and --version cannot be used together")
    if args.sdist_local is not None and args.version is None:
        raise SdistError("--sdist-local requires --version")
    artifact_dir = args.archive_dir or args.report.with_name(args.report.stem + "-artifacts")
    _progress("Starting investigation...")
    with _repository(args) as (repo, source):
        _progress("Resolving ref {}...".format(args.ref))
        resolved, commits, shallow = resolve_history(repo, args.ref)
        _progress("Reading project metadata at {}...".format(resolved[:12]))
        package = read_project_name(repo, resolved)
        configured_about_path = read_version_path(repo, resolved)
        _progress("Checking PyPI and listing releases for {}...".format(package))
        index = pypi_project_files(package, args.sdist_pypi)
        versions = [args.version] if args.version else sdist_versions(index, package)
        _progress("Found {} release(s); comparing against {} first-parent commit(s).".format(len(versions), len(commits)))
        report = {
            "tool": "sdist2tag",
            "package": package,
            "target_ref": args.ref,
            "resolved_ref_commit": resolved,
            "repository_source": source,
            "pypi_index_url": index["url"],
            "comparison_rule": RULE_ID,
            "history": {"method": "first-parent-oldest-first", "shallow": shallow, "commits": len(commits)},
            "archive_dir": str(artifact_dir),
            "results": [],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        cache = CompareCache()
        about_path = None
        about_versions = None
        about_index = None
        for position, version in enumerate(versions, start=1):
            row = {"version": version}
            _progress("[{}/{}] {}: retrieving and validating sdist...".format(position, len(versions), version))
            try:
                sdist = _get_sdist(index, package, version, args.sdist_local, artifact_dir)
                row["sdist"] = {
                    "name": sdist.name,
                    "url": sdist.url,
                    "sha256": sdist.sha256,
                    "size": sdist.size,
                    "archive_path": sdist.archive_path,
                    "excluded": sdist.excluded,
                }
                if about_versions is None:
                    about_paths = [
                        path for path in sdist.files
                        if path == "__about__.py" or path.endswith("/__about__.py")
                    ]
                    candidate_path = configured_about_path
                    if candidate_path not in sdist.files:
                        candidate_path = about_paths[0] if len(about_paths) == 1 else None
                    if candidate_path is not None and literal_about_version(sdist.files[candidate_path]) is not None:
                        about_path = candidate_path
                        _progress("Indexing literal __version__ values in {} across {} commits...".format(about_path, len(commits)))
                        about_versions = index_about_versions(
                            repo, commits, about_path, cache,
                            progress=lambda completed, total: _progress(
                                "Version index: read {}/{} commits...".format(completed, total)
                            ),
                        )
                        about_index = AboutVersionIndex.build(commits, about_versions)
                if shallow:
                    row["status"] = "needs_investigation"
                    row["message"] = "shallow repository cannot establish the earliest match"
                else:
                    expected_version = (
                        literal_about_version(sdist.files[about_path])
                        if about_path is not None and about_path in sdist.files
                        else None
                    )
                    candidate_commits = about_index.candidates(expected_version) if about_index is not None else commits
                    skipped = len(commits) - len(candidate_commits)
                    if skipped:
                        _progress("[{}/{}] {}: comparing {} candidate commits; {} filtered by version.".format(
                            position, len(versions), version, len(candidate_commits), skipped
                        ))
                    row.update(compare(
                        repo, candidate_commits, sdist, cache,
                        history_commit_count=len(commits),
                        skipped_by_version=skipped,
                        progress=lambda scanned, total, compared, filtered, v=version, p=position: _progress(
                            "[{}/{}] {}: compared {}/{} candidate commits (version-filtered {}).".format(
                                p, len(versions), v, scanned, total, filtered
                            )
                        ),
                    ))
            except (SdistError, OSError) as exc:
                row.update({"status": "error", "message": str(exc)})
            report["results"].append(row)
            _progress("[{}/{}] {}: {}.".format(position, len(versions), version, row.get("status", "error")))
            # Persist each completed entry so an interrupted long run still leaves useful progress.
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in report["results"]:
        _print_ascii("{} {}{}".format(row["version"], row.get("status", "error"),
                                      " " + str(row.get("selected_commit")) if row.get("selected_commit") else ""))
    _print_ascii("Report: {}".format(args.report))
    return 0 if all(row.get("status") == "match" for row in report["results"]) else 1


def _print_ascii(message: str) -> None:
    safe = message.encode("ascii", "backslashreplace").decode("ascii")
    print(safe)


def _progress(message: str) -> None:
    safe = message.encode("ascii", "backslashreplace").decode("ascii")
    print("sdist2tag: " + safe, file=sys.stderr, flush=True)


def _tag(args: argparse.Namespace) -> int:
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SdistError("could not read report: {}".format(exc))
    if report.get("tool") != "sdist2tag" or report.get("comparison_rule") != RULE_ID:
        raise SdistError("unsupported or invalid sdist2tag report")
    if not isinstance(report.get("results"), list) or not isinstance(report.get("resolved_ref_commit"), str):
        raise SdistError("report is missing required investigation data")
    repo = args.repo_local.resolve()
    _, commits, shallow = resolve_history(repo, report.get("resolved_ref_commit", "HEAD"))
    if shallow:
        raise GitError("cannot create tags from a shallow repository")
    available = set(commits)
    for row in report["results"]:
        commit = row.get("selected_commit")
        if row.get("status") == "match" and commit not in available:
            raise GitError("reported commit is not on the repository first-parent history: {}".format(commit))
        if row.get("status") == "match" and (not row.get("sdist", {}).get("name") or not row.get("sdist", {}).get("sha256")):
            raise SdistError("matched report entry is missing sdist metadata")
    create_tags(repo, report["results"], report["package"], str(args.report), args.tag_prefix)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in report["results"]:
        if "tag" in row:
            _print_ascii("{} {}".format(row["tag"]["name"], row["tag"]["status"]))
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "investigate":
            return _investigate(args)
        return _tag(args)
    except (SdistError, GitError) as exc:
        _print_ascii("error: {}".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
