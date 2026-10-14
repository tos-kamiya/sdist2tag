# sdist2tag

`sdist2tag` compares the files in published PyPI source distributions with a
Git repository's first-parent history. It selects the oldest matching commit
and can create annotated local tags from a saved report. A match shows that a
commit reproduces the published source files; it does not prove which commit
was used to build the release. CPython 3.10 or newer is required.

## Use cases

- Check whether each version published on PyPI is preserved in the repository's
  commit history.
- Find and create Git tags in one batch when tags for versions published on
  PyPI were forgotten.

## Installation

```console
pipx install git+https://github.com/ToshihiroKamiya/sdist2tag.git
```

## Investigate

Choose a GitHub repository or a local repository root. The project name comes
from `[project].name` in `pyproject.toml` at the selected ref, and the project
must be published on PyPI.

```console
sdist2tag investigate --repo-github https://github.com/example/project
sdist2tag investigate --repo-local /path/to/project --report report.json
```

All published releases are investigated by default. Add `--version` to limit
the run. `--all` is accepted as an explicit spelling of the default. PyPI is
queried from the project name; `--sdist-pypi` can specify a matching PyPI
project page or project API URL.

```console
sdist2tag investigate --repo-local /path/to/project --version 1.2.3
sdist2tag investigate --repo-local /path/to/project --version 1.2.3 \
  --sdist-local /tmp/project-1.2.3.tar.gz --report report.json
```

`--sdist-local` accepts one archive and still verifies it against PyPI's
published SHA-256. It requires `--version`; directories such as `dist/` are not
scanned. Downloaded archives are retained beside the report in a
`*-artifacts/` directory unless `--archive-dir` is supplied.

The tool compares archive files byte for byte, excluding only the generated
top-level `PKG-INFO`. It does not run package code or build hooks. Unsafe paths
and special archive members require investigation. A shallow local repository
cannot establish the earliest match and is reported accordingly. GitHub
repositories are cloned as temporary bare repositories with full history.

## Create tags

Tag creation is separate and consumes an investigation report:

```console
sdist2tag tag --repo-local /path/to/project --report report.json --tag-prefix v
```

All target tag names are checked before creation. An existing tag at the
detected commit is already correct; a same-name tag at another commit is an
error. Existing tags are never moved or deleted, and tags are not pushed.

## License

`sdist2tag` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
