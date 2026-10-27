# GitHub setup and releases

This repository is prepared for a public MIT-licensed project. Publishing is a separate maintainer action. Local preparation does not create a GitHub repository, upload code, publish packages, or change a running installation.

## First publication

1. Review the staged files and the release-boundary check. Instance configuration, `.env` files, provider credentials, private operations notes, databases, generated builds and `.validation/` must remain excluded.
2. Set the Git name/email you want in the public commit history; use your GitHub no-reply address if preferred. Make the initial commit on `main`.
3. Create an empty GitHub repository under your account or organization. Keep its name consistent with the image/documentation examples, or update them.
4. Add its real remote URL and push `main`. Replace the placeholders below; no remote is preconfigured.

```sh
git diff --cached --stat
python scripts/check_public_files.py
git commit -m "Prepare QuillSail for open source"
git remote add origin https://github.com/YOUR_ACCOUNT/copydesk.git
git push -u origin main
```

In repository settings, enable Issues, private vulnerability reporting, Dependabot alerts/updates and secret push protection where available. Set branch rules for `main` that require passing CI and review appropriate to the maintainer team. Add real maintainer/support contacts to SECURITY.md and CODE_OF_CONDUCT.md when those are established. Do not put a production SSH key, SMTP password, instance secret key or database URL into GitHub Actions; this project's CI needs none of them.

GitHub settings depend on account/repository capabilities and must be configured by the owner. See [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository).

## CI

`.github/workflows/tests.yml` runs on pushes and pull requests. It checks Python 3.11–3.14, PostgreSQL/MariaDB, lint, public-file boundaries, wheel/sdist metadata, a clean wheel installation, and a Docker/Compose smoke deployment. Action versions are pinned to commit hashes; Dependabot proposes updates for actions, Python dependencies and the base image.

Pull-request CI has read-only repository permission and no production credentials. The container test uses its own disposable Compose project/volume and synthetic first-run setup. The default source build and all release archives exclude local secrets and instance data.

## Release a version

1. Update `copydesk.__version__`, the changelog and verification notes. Run the relevant tests and package/container checks. Keep the version immutable after publication.
2. Push the reviewed commit and a matching tag such as `v1.3.0`.
3. Publish a GitHub release for that tag. Mark beta releases as prereleases. Merely pushing `main` or a tag does not run the publishing workflow.

The Release workflow requires the tag to equal `v` plus the package version. It validates the source, runs tests and a clean package install, then attaches the wheel and source archive. Its image job builds and smoke-tests a container before publishing to `ghcr.io/<owner>/<repository>:<version>` using GitHub's built-in token. Stable releases can also update `latest`; prereleases do not. The initial registry image targets Linux amd64.

After the first image publication, check that the package is linked to the repository and has the intended public visibility before advertising anonymous pulls. The workflow does not publish to PyPI, SSH into servers or deploy automatically. Package publication on another registry requires ownership and an explicit maintainer configuration.

See GitHub's [container publishing guide](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images). Users deploy the image or source using [installation](installation.md), then manage backups/upgrades with [maintenance](maintenance.md).

## Local release checks

```sh
pip install -e '.[dev,integration]'
pytest
ruff check src tests scripts deploy
python scripts/check_public_files.py
python -m build
python -m twine check --strict dist/*
python scripts/check_public_files.py --archive dist/*.whl
python scripts/check_public_files.py --archive dist/*.tar.gz
python scripts/package_smoke.py dist/*.whl
docker build -t copydesk:ci .
bash scripts/container_smoke.sh
```

Use a clean output directory when building: release workflows start from fresh checkouts, whereas a developer's local `dist/` may contain older versions. The boundary checker catches excluded file types and recognizable key/token formats; also review staged content for organization-specific/private information.
