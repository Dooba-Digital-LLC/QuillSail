"""Compose lifecycle adapter for the same signed server-update transaction."""

from __future__ import annotations

import json
import re
import shutil
import uuid

from copydesk.desktop import write_private
from copydesk.updater import UpdateError


class ComposeDeployment:
    def __init__(self, profile, run):
        self.profile, self.run = profile, run
        self.override = profile.at("install_root") / "current-image.json"

    def command(self, *arguments):
        base = ["docker", "compose", "--project-name", self.profile.values["compose_project"],
                "-f", self.profile.at("compose_file")]
        if self.override.exists():
            base += ["-f", self.override]
        return self.run([*base, *arguments])

    def version(self):
        return self.command("exec", "-T", "web", "python", "-c",
                            "import importlib.metadata; print(importlib.metadata.version('copydesk'))").strip()

    def preflight(self):
        self.run(["docker", "compose", "version"])
        from copydesk.server_update import trusted_file

        trusted_file(self.profile.at("compose_file"))
        for name in ("web", "worker"):
            if not self.command("ps", "-q", name).strip():
                raise UpdateError("Start the registered Compose web and worker services before updating.")

    def _images(self):
        result = {}
        for name in ("web", "worker"):
            container = self.command("ps", "-q", name).strip()
            image = self.run(["docker", "inspect", "--format", "{{.Image}}", container]).strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
                raise UpdateError("The current container image could not be pinned for recovery.")
            result[name] = image
        result["init"] = result["web"]
        return result

    def prepare(self, wheel, version, transaction):
        context = transaction / "image-build"
        context.mkdir(mode=0o700)
        shutil.copy2(wheel, context / wheel.name)
        dockerfile = "\n".join([
            "FROM " + self.profile.values["base_image"],
            "RUN groupadd --gid 10001 copydesk && useradd --uid 10001 --gid 10001 --no-create-home copydesk && mkdir -p /var/lib/copydesk && chown 10001:10001 /var/lib/copydesk",
            "COPY " + wheel.name + " /tmp/" + wheel.name,
            f'RUN python -m pip install --no-cache-dir --only-binary=:all: "/tmp/{wheel.name}[server,postgres,mysql]" && rm /tmp/{wheel.name}',
            "USER 10001:10001", "WORKDIR /var/lib/copydesk",
            "ENV COPYDESK_CONFIG=/var/lib/copydesk/copydesk.toml PYTHONUNBUFFERED=1",
            'ENTRYPOINT ["python", "-m", "copydesk.container"]', 'CMD ["web"]', "",
        ])
        (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        tag = "quillsail-update:" + version + "-" + uuid.uuid4().hex[:12]
        self.run(["docker", "build", "--tag", tag, str(context)], timeout=1800)
        capabilities = json.loads(self.run(["docker", "run", "--rm", tag, "update", "capabilities"]))
        if capabilities.get("server_update_protocol") != 1 or not capabilities.get("maintenance_gate") or capabilities.get("version") != version:
            raise UpdateError("The candidate container does not support safe update validation.")
        return {"old_images": self._images(), "new_image": tag}

    def activate(self, state, *, previous=False):
        images = state["old_images"] if previous else dict.fromkeys(("web", "worker", "init"), state["new_image"])
        write_private(self.override, json.dumps({"services": {name: {"image": image} for name, image in images.items()}}))

    def stop(self):
        self.command("stop", "worker", "web")

    def start(self, *, worker=False):
        self.command("up", "-d", "--no-deps", "--no-build", "worker" if worker else "web")

    def migrate(self):
        self.command("run", "--rm", "--no-deps", "init")

    def worker_active(self):
        return bool(self.command("ps", "--status", "running", "-q", "worker").strip())

    def ownership(self):
        return int(self.profile.values.get("service_uid", 10001)), int(self.profile.values.get("service_gid", 10001))
