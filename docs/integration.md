# Integrating ZeroNotebookLM

ZeroNotebookLM keeps the upstream-compatible `notebooklm` Python import and CLI
names. Use the installed package for Python code or the generated single-file
artifact for a vendored CLI.

## Install

From a checkout:

```sh
python -m pip install --no-build-isolation --no-deps .
```

Directly from GitHub:

```sh
python -m pip install --no-build-isolation --no-deps \
  "zero-notebooklm @ git+https://github.com/0xTitanas/zero-notebooklm.git@main"
```

Pin a release tag or commit instead of `main` for reproducible deployments.
The runtime has no third-party Python-package dependencies.

## Authenticate on Windows

Interactive Edge login is the recommended Windows path:

```powershell
notebooklm login --browser msedge
notebooklm status --paths
notebooklm list --json
```

Chrome works the same way:

```powershell
notebooklm login --browser chrome
```

To import an existing signed-in browser session, close the browser first and run:

```powershell
notebooklm login --browser-cookies edge
# or
notebooklm login --browser-cookies chrome
```

ZeroNotebookLM supports current Windows Chrome and Edge cookie stores. Those
paths remain excluded from strict pinned-reference parity because the pinned
upstream reader cannot execute the same modern profiles.

## Use the CLI from another application

Prefer JSON output when calling the CLI from a script:

```python
import json
import subprocess

result = subprocess.run(
    ["notebooklm", "list", "--json"],
    check=True,
    capture_output=True,
    text=True,
)
notebooks = json.loads(result.stdout)["notebooks"]
```

Set `NOTEBOOKLM_HOME` to give an application its own auth/config directory. Set
`NOTEBOOKLM_PROFILE` and `NOTEBOOKLM_NOTEBOOK` when a process should always use a
specific profile or notebook.

## Use the Python API

The import path intentionally remains compatible with `notebooklm-py`:

```python
import asyncio

from notebooklm import NotebookLMClient


async def main() -> None:
    async with NotebookLMClient.from_storage() as client:
        for notebook in await client.notebooks.list():
            print(notebook.id, notebook.title)


asyncio.run(main())
```

After selecting a notebook, other subclients are available through
`client.sources`, `client.chat`, `client.notes`, `client.artifacts`,
`client.research`, and `client.sharing`.

## Vendor the single-file CLI

Generate and copy the standalone artifact:

```sh
python scripts/build_singlefile.py
cp singlefile/zero_notebooklm.py your-project/tools/
python your-project/tools/zero_notebooklm.py --help
```

The single-file artifact is a CLI, not a replacement import package. Install the
distribution when your project needs the Python API.

## Keep credentials out of source control

Do not commit the directory reported by `notebooklm status --paths`. Keep session
files, cookies, account emails, notebook IDs, and generated exports out of logs,
fixtures, and repositories. Use a dedicated `NOTEBOOKLM_HOME` for CI or service
accounts and supply it through the deployment environment.
