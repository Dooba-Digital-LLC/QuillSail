# Desktop launcher

Open **QuillSail** to start a local publication and open its dashboard in your browser. The packaged app includes Python and its dependencies. You do not need a terminal, Docker, or a configuration file to use it.

On the first launch, enter your publication name, choose Newsletter / Blog / Blog + newsletter, and create your administrator account. The launcher authorizes this first setup automatically; there is no setup code to retrieve. Finishing the form signs you into the dashboard. Future launches reopen your publication; your remembered browser session keeps you signed in, or you can sign in with your account.

## Using the app

Download the beta from the [GitHub release](https://github.com/Dooba-Digital-LLC/QuillSail/releases/tag/v1.7.0). Four separate downloads cover Apple Silicon Macs (`macos-arm64`), Intel Macs (`macos-x86_64`), 64-bit Windows (`windows-x86_64`), and 64-bit Linux (`linux-x86_64`). `SHA256SUMS.txt` contains the archive hashes; `desktop-builds.json` records the source commit and native verification results.

Extract the bundle for your operating system. On macOS, move **QuillSail.app** to Applications and open it. On Windows, keep the extracted folder together and open **QuillSail.exe**. On Linux, keep the extracted folder together and open **QuillSail**. Use a build matching your CPU architecture.

Linux builds use Ubuntu 22.04 and require a desktop environment. On a minimal Ubuntu installation, missing Qt/X11 libraries can be installed with `sudo apt-get install libegl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-render-util0 libxcb-xfixes0`. Other distributions need the equivalent packages. Windows builds target x64 Windows; Windows ARM and 32-bit systems are not included.

The launcher starts automatically. **Open dashboard** reopens your browser, **Stop** finishes background work and stops the local website, and **Start** starts it again. **View logs** opens the private local log, while **Open data folder** shows the files to back up. Closing the launcher stops its website and worker; your content stays saved. The launcher uses an available port on `127.0.0.1`, remembers it, and stays accessible only on your computer.

Email initially stays in local `.eml` files. Configure a sender in Settings → Email when you want actual delivery. Local URLs work only on your computer; [server installation](installation.md) covers a public website and an always-running email worker. Connecting Gmail/Outlook still needs the provider registration described in [email connectors](email-connectors.md).

## Data and updates

The app keeps its publication outside the application bundle, so replacing the app preserves your account, settings, secret key, content and uploaded files:

| System | Default data folder |
| --- | --- |
| macOS | `~/Library/Application Support/QuillSail` |
| Windows | `%LOCALAPPDATA%\QuillSail` |
| Linux | `$XDG_DATA_HOME/quillsail`, or `~/.local/share/quillsail` |

Quit QuillSail before backing up or replacing the app. Copy the complete data folder to protected storage. Before a schema upgrade, the launcher automatically saves a consistent SQLite database backup, configuration and uploaded files under `backups/`. If startup reports an upgrade failure, preserve that backup and consult [maintenance](maintenance.md) before retrying or restoring an older app. Do not restore pending mail jobs into an active sender without reviewing them.

Each launcher owns one desktop publication. Repeated starts cannot create a second worker for the same folder. Desktop configuration is isolated from server environment variables and existing CLI/server installations. Advanced users can select a separate empty folder with `quillsail desktop --data-dir PATH`.

## Building a bundle

Build on the target operating system and CPU; these are native bundles, not cross-compilations. From a clean checkout, build/install the wheel and the optional desktop dependencies in a virtual environment:

```sh
python -m pip install build
python -m build --wheel
python -m pip install 'dist/copydesk-VERSION-py3-none-any.whl[desktop-build]'
python scripts/build_desktop.py
```

Replace VERSION with the built version. Bundles appear in `dist/desktop/`. The build includes package resources, Python, the native Qt libraries and dependency license files. It runs disposable browser setup/restart and native GUI start/stop/restart checks using the bundled executable; it sends no mail. Run the checks independently with `QuillSail --smoke-test` and `QuillSail --smoke-test-gui`. For source development, install `.[desktop]` and run `quillsail desktop` or `quillsail-desktop`.

On Intel macOS, install the build dependencies in a clean environment with `OPENSSL_STATIC=1 python -m pip install --no-cache-dir 'dist/copydesk-VERSION-py3-none-any.whl[desktop-build]'`. This keeps a source-built cryptography binding independent of Python's OpenSSL library. The build rejects dynamically linked macOS bindings, which can collide when bundled. See [Cryptography's installation guide](https://cryptography.io/en/latest/installation/).

The **Desktop bundles** GitHub Actions workflow explicitly builds all four targets on native runners. Pull requests and ordinary manual runs only save workflow artifacts. A maintainer can run it on `main` with **publish** enabled to publish a desktop beta after every target passes. It validates source commits and hashes, stages all assets in a draft, then publishes the complete release. A failed upload leaves a resumable draft. Published versions cannot be overwritten by the script. This workflow publishes desktop downloads only; it does not publish containers or packages to a registry.

Local macOS builds are ad-hoc signed. Public download distribution still needs Developer ID signing/notarization, and Windows distribution should use an appropriate code-signing process. These release credentials are not part of the repository. No signing identity or notarization account is configured by the build script.

The launcher uses [Qt for Python](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html) and [PyInstaller](https://pyinstaller.org/en/stable/usage.html). Retain the bundled dependency licenses when redistributing; their licenses remain separate from QuillSail's MIT license.
