# Maintaining signed desktop and server updates

The desktop updater uses the maintained [python-tuf client](https://theupdateframework.readthedocs.io/en/latest/api/tuf.ngclient.updater.html). Each package ships a trusted root under `copydesk/updates/root.json`. Root signatures require two of three offline Ed25519 keys. Separate online targets, snapshot and timestamp keys authenticate release metadata. Archives must match the signed length and SHA-256 digest; expired metadata, metadata rollback, mismatched processors and application downgrades are rejected.

Desktop bundles include a certifi CA bundle for HTTPS verification, in addition to Python’s available system or configured trust roots. This avoids relying only on certificate paths from the build machine. HTTPS certificate verification remains enabled.

The TUF root is public and belongs in the repository. Private signing keys must remain outside Git, wheels, containers, desktop applications and logs. Losing the online keys requires a root rotation signed by the existing offline root threshold; do not replace the shipped root with an unrelated root or silently generate new release keys.

## One-time setup

For a new distribution or fork, create your own trust root before building any updater-enabled applications:

```sh
python scripts/update_feed.py init --keys /protected/quillsail-update-keys.json
```

This refuses to overwrite existing keys or a root. Back up the private file securely. Store its `root` keys offline. Configure the repository Actions secret `DESKTOP_UPDATE_KEYS` with only the `targets`, `snapshot` and `timestamp` arrays from the file's `keys` object, as a JSON object. The secret must not contain the offline root keys. Limit release workflow access to trusted maintainers; protect the default branch and review workflow changes.

Forks must also change `REPOSITORY` and `METADATA_URL` in `copydesk/updater.py` to their own distribution before shipping their root. A fork must not advertise its own binaries using the upstream distribution's keys or feed. The public root currently in this repository belongs to the upstream QuillSail distribution; existing roots are never regenerated as part of a normal build.

## Publishing and renewal

Run the **Desktop bundles** workflow on `main` with publication explicitly enabled. Each of the four native builders must pass browser/database restart checks, GUI lifecycle checks, and the separate installer smoke test. The server builder verifies wheel/sdist contents, a clean package installation, and a real disposable web/worker update and recovery. The publisher validates all reports, publishes immutable versioned application downloads plus the server wheel/source, and signs the Beta feed with the configured online keys. It refuses publication if signing credentials are absent.

The signed `CHANNEL/server` target binds the exact wheel, digest, length, version,
commit and server-update protocol. The client reauthenticates metadata before
download. See [server updates](server-updates.md) for its separate deployment
transaction and recovery rules. Keep `server-build.json` and the tested wheel
alongside desktop reports when promoting a release to another channel.

The fixed `desktop-updates` GitHub release hosts versioned TUF root, targets and snapshot metadata plus `timestamp.json`. Channel and platform are signed virtual target names; their custom metadata binds the exact immutable release archive name and version. The client passes those verified hashes and lengths to TUF's download verifier. Timestamp is uploaded last, so an interrupted publication cannot authorize incomplete downloads. Versioned metadata is retained for cached clients and root rotation.

The **Refresh signed desktop update metadata** workflow runs daily and can be run manually. It renews metadata without rebuilding or replacing application downloads. Timestamp metadata expires after seven days, snapshots after fourteen days and targets after ninety days. Root metadata expires after five years and requires an offline root-rotation ceremony before expiry. Monitor workflow failures; metadata expiry intentionally prevents updates until a maintainer repairs and renews the feed.

To promote a fully tested release to the Stable channel, use its original verified archives and `desktop-builds.json`:

```sh
python scripts/update_feed.py publish --downloads /protected/tested-desktop-downloads --channel stable
```

This uses `GH_REPO`, `GH_TOKEN` and `DESKTOP_UPDATE_KEYS` from the protected release environment. Keep metadata refresh and release publication serialized through the `desktop-update-feed` workflow concurrency group.

## Installation and recovery

The installer is a separate small native executable. The launcher copies it to a private staging folder beside the app so Windows executable locks cannot prevent replacement. A writable application parent and enough free disk space are required. Neither downloads nor application files are written into the publication folder. The helper waits for the launcher process, acquires the application lock, verifies the archive again, rejects unsafe paths, external symlinks and excessive expansion, verifies extracted Mac code signatures, then renames application folders on the same filesystem. A failed replacement restores the old application.

The previous application stays under the transaction's `previous` directory for manual recovery. The launcher surfaces recorded installation failures on its next opening. No automatic database downgrade is attempted. Publication backups created before schema migrations must be restored together with the matching older application when recovering from an incompatible migration.

TUF authenticates release content independently of operating-system code signing. Released Mac apps are also signed with a Developer ID and notarized by Apple, as described in the [desktop guide](desktop.md#mac-signing-and-notarization); Windows Authenticode signing isn't configured. Never describe TUF signatures as Apple notarization or Windows publisher verification.
