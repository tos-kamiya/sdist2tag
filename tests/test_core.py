import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from sdist2tag.core import (
    AboutVersionIndex, GitError, SdistError, _extract_archive, compare, create_tags,
    index_about_versions, pypi_index_url, pypi_project_files, read_version_path, resolve_history,
    sdist_file, sdist_versions,
)
from sdist2tag.cli import _parser, main


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo("pkg-1/" + name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return output.getvalue()


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")

    def tearDown(self):
        self.temp.cleanup()

    def test_investigation_defaults_to_all_releases(self):
        args = _parser().parse_args(["investigate", "--repo-local", str(self.repo)])
        self.assertIsNone(args.version)
        self.assertFalse(args.all)
        limited = _parser().parse_args(["investigate", "--repo-local", str(self.repo), "--version", "1.2"])
        self.assertEqual(limited.version, "1.2")
        tag_args = _parser().parse_args(["tag", "--repo-local", "."])
        self.assertEqual(tag_args.report, Path("sdist2tag-report.json"))

    def test_pypi_project_page_url_maps_to_simple_api_and_checks_name(self):
        self.assertEqual(
            pypi_index_url("my_pkg", "https://pypi.org/project/My.Pkg/#history"),
            "https://pypi.org/simple/my-pkg/",
        )
        with self.assertRaisesRegex(SdistError, "does not match"):
            pypi_index_url("my_pkg", "https://pypi.org/project/other/")
        with self.assertRaisesRegex(SdistError, "HTTPS URL"):
            pypi_index_url("my_pkg", "http://example.com/project/my-pkg/")

    def test_sdist_versions_and_file_selection_use_supported_archives(self):
        index = {
            "versions": ["1.0", "2.0"],
            "files": [
                {"filename": "my_pkg-1.0.tar.gz", "url": "https://files/1", "hashes": {"sha256": "a"}},
                {"filename": "my_pkg-2.0-py3-none-any.whl", "url": "https://files/wheel", "hashes": {"sha256": "b"}},
            ],
        }
        self.assertEqual(sdist_versions(index, "my_pkg"), ["1.0", "2.0"])
        self.assertEqual(sdist_file(index, "my_pkg", "1.0")["url"], "https://files/1")
        with self.assertRaisesRegex(SdistError, "no supported"):
            sdist_file(index, "my_pkg", "2.0")

    def test_about_version_index_skips_irrelevant_commits_without_changing_match(self):
        self.repo.joinpath("src/pkg").mkdir(parents=True)
        (self.repo / "src/pkg/__about__.py").write_text('__version__ = "1.0"\n', encoding="utf-8")
        (self.repo / "source.txt").write_text("same\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "version 1")
        first = self.git("rev-parse", "HEAD")
        (self.repo / "src/pkg/__about__.py").write_text('__version__ = "2.0"\n', encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "version 2")
        second = self.git("rev-parse", "HEAD")
        _, commits, _ = resolve_history(self.repo, "main")
        sdist = _extract_archive(archive({
            "src/pkg/__about__.py": b'__version__ = "2.0"\n',
            "source.txt": b"same\n",
        }), "pkg-2.0.tar.gz", "", "")
        versions = index_about_versions(self.repo, commits, "src/pkg/__about__.py")
        index = AboutVersionIndex.build(commits, versions)
        candidates = index.candidates("2.0")
        result = compare(
            self.repo, candidates, sdist,
            history_commit_count=len(commits),
            skipped_by_version=len(commits) - len(candidates),
        )
        self.assertEqual(result["selected_commit"], second)
        self.assertEqual(result["comparison"]["commits_skipped_by_about_version"], 1)
        self.assertEqual(versions[first], "1.0")

    def test_configured_hatch_version_path_selects_the_project_about_file(self):
        (self.repo / "pyproject.toml").write_text(
            '[tool.hatch.version]\npath = "src/pkg/__about__.py"\n', encoding="utf-8"
        )
        self.git("add", "pyproject.toml")
        self.git("commit", "-qm", "configure version file")
        commit = self.git("rev-parse", "HEAD")
        self.assertEqual(read_version_path(self.repo, commit), "src/pkg/__about__.py")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo)] + list(args)).decode().strip()

    def commit(self, filename, content, date):
        (self.repo / filename).write_bytes(content)
        self.git("add", filename)
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.check_call(["git", "-C", str(self.repo), "commit", "-qm", "snapshot"], env=dict(__import__("os").environ, **env))
        return self.git("rev-parse", "HEAD")

    def test_selects_oldest_first_parent_match_and_ignores_repo_only_files(self):
        first = self.commit("a.txt", b"source\n", "2001-01-01T00:00:00+00:00")
        extra_only = self.commit("extra.txt", b"not in sdist", "2000-01-01T00:00:00+00:00")
        self.commit("a.txt", b"temporarily different\n", "1998-01-01T00:00:00+00:00")
        latest = self.commit("a.txt", b"source\n", "1999-01-01T00:00:00+00:00")
        resolved, commits, shallow = resolve_history(self.repo, "main")
        sdist = _extract_archive(archive({"a.txt": b"source\n"}), "pkg.tar.gz", "https://example.invalid/pkg.tar.gz", "")
        result = compare(self.repo, commits, sdist)
        self.assertFalse(shallow)
        self.assertEqual(resolved, latest)
        self.assertEqual(result["selected_commit"], first)
        self.assertEqual(result["matching_commits"], [first, extra_only, latest])

    def test_generated_pkg_info_is_the_only_exclusion(self):
        data = archive({"PKG-INFO": b"generated", "a.txt": b"x"})
        sdist = _extract_archive(data, "pkg.tar.gz", "", hashlib.sha256(data).hexdigest())
        self.assertEqual(sdist.excluded, [{"path": "PKG-INFO", "reason": "Generated by the package build backend."}])
        self.assertEqual(set(sdist.files), {"a.txt"})
        with self.assertRaisesRegex(SdistError, "SHA-256 mismatch"):
            _extract_archive(data, "pkg.tar.gz", "", "0" * 64)

    def test_merged_branch_match_is_selected_at_merge_on_first_parent(self):
        self.commit("a.txt", b"old\n", "2001-01-01T00:00:00+00:00")
        self.git("checkout", "-qb", "side")
        branch_match = self.commit("a.txt", b"published\n", "1990-01-01T00:00:00+00:00")
        self.git("checkout", "-q", "main")
        self.commit("main.txt", b"main", "2002-01-01T00:00:00+00:00")
        self.git("merge", "--no-ff", "-qm", "merge side", "side")
        merge_commit = self.git("rev-parse", "HEAD")
        _, commits, _ = resolve_history(self.repo, "main")
        sdist = _extract_archive(archive({"a.txt": b"published\n"}), "pkg.tar.gz", "", "")
        result = compare(self.repo, commits, sdist)
        self.assertNotIn(branch_match, commits)
        self.assertEqual(result["selected_commit"], merge_commit)

    def test_rejects_unsafe_or_special_members(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            info = tarfile.TarInfo("pkg-1/../escape")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(SdistError, "unsafe archive path"):
            _extract_archive(output.getvalue(), "bad.tar.gz", "", "")

    def test_tag_conflicts_are_checked_before_any_tag_is_created(self):
        target = self.commit("a.txt", b"source\n", "2001-01-01T00:00:00+00:00")
        other = self.commit("a.txt", b"other\n", "2002-01-01T00:00:00+00:00")
        self.git("tag", "-a", "v1", other, "-m", "existing")
        results = [
            {"version": "1", "status": "match", "selected_commit": target, "sdist": {"name": "pkg.tar.gz", "sha256": "abc", "excluded": []}},
            {"version": "2", "status": "match", "selected_commit": target, "sdist": {"name": "pkg.tar.gz", "sha256": "def", "excluded": []}},
        ]
        with self.assertRaisesRegex(GitError, "tag conflict for v1"):
            create_tags(self.repo, results, "pkg", "report.json", "v")
        self.assertNotEqual(self.git("rev-parse", "v1^{}"), target)
        self.assertNotEqual(subprocess.call(["git", "-C", str(self.repo), "show-ref", "--verify", "--quiet", "refs/tags/v2"]), 0)

    def test_existing_tag_for_detected_commit_is_reported_as_already_correct(self):
        target = self.commit("a.txt", b"source\n", "2001-01-01T00:00:00+00:00")
        self.git("tag", "-a", "v1", target, "-m", "existing")
        results = [{
            "version": "1",
            "status": "match",
            "selected_commit": target,
            "sdist": {"name": "pkg.tar.gz", "sha256": "abc", "excluded": []},
        }]
        create_tags(self.repo, results, "pkg", "report.json", "v")
        self.assertEqual(results[0]["tag"]["status"], "existing_same_commit")

    def test_tag_subcommand_creates_from_report_and_records_outcome(self):
        target = self.commit("a.txt", b"source\n", "2001-01-01T00:00:00+00:00")
        report_path = self.repo / "report.json"
        report_path.write_text(json.dumps({
            "tool": "sdist2tag",
            "package": "pkg",
            "target_ref": "main",
            "resolved_ref_commit": target,
            "comparison_rule": "sdist-files-v1",
            "results": [{
                "version": "1",
                "status": "match",
                "selected_commit": target,
                "sdist": {"name": "pkg.tar.gz", "sha256": "abc", "excluded": []},
            }],
        }), encoding="utf-8")
        self.assertEqual(main(["tag", "--repo-local", str(self.repo), "--report", str(report_path)]), 0)
        updated = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["results"][0]["tag"]["status"], "created")
        self.assertEqual(self.git("rev-parse", "v1^{}"), target)


if __name__ == "__main__":
    unittest.main()
