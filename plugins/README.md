# Custom Nautobot Apps

Put Nautobot apps that aren't installed from PyPI in this folder. During the image build, every file here is copied in and installed alongside Nautobot.

## Requirements

- **Wheel files only.** Each app must be a built `.whl` file, e.g. `my_app-1.0.0-py3-none-any.whl`. Source folders and `.tar.gz` archives are not supported.
- **Listed in `pyproject.toml`.** A wheel in this folder is only installed if it has an entry in `pyproject.toml` (see below).
- **Compatible with the image.** The app must support the Nautobot version and Python version pinned in `pyproject.toml`. Check the `nautobot` and `Requires-Python` constraints in the app's own metadata.
- **Installable dependencies.** Everything the app depends on must be available from PyPI, or be another wheel in this folder that is also listed in `pyproject.toml`.

## Adding an app

1. Build the wheel from the app's repository:

   ```bash
   poetry build --format wheel
   ```

   The wheel is written to the app's `dist/` folder.

2. Copy the `.whl` file into this folder.

3. Add it to `pyproject.toml`, under `[tool.poetry.dependencies]` in the "Add Custom Dependencies Here" section:

   ```toml
   my-app = { path = "plugins/my_app-1.0.0-py3-none-any.whl" }
   ```

   - The name on the left is the app's package name, as published in its metadata (e.g. `nautobot-ssot`), not the file name.
   - The path must match the file name exactly, including the version.

4. Enable the app in Nautobot by adding it to `PLUGINS` in `nautobot_config.py`. Installing an app doesn't enable it.

## Updating or removing an app

- **Updating:** replace the `.whl` file, and update the file name in the `path` in `pyproject.toml` to match.
- **Removing:** delete the `.whl` file, its line in `pyproject.toml`, and its entry in `PLUGINS`.

If `pyproject.toml` points at a file that isn't in this folder, the image build fails with `Path ... does not exist`.
