"""Install a Linux user service for a Boti worker."""

import argparse
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

from boti.worker import WorkerConfig


def unit_quote(value, *, exec_argument=False):
    value = str(value)
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Service paths must be single-line values")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if exec_argument:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def service_text(python, project, config, path_env):
    return "\n".join(
        [
            "[Unit]",
            "Description=Boti clipping worker",
            "StartLimitIntervalSec=0",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={unit_quote(project)}",
            f"ExecStart={unit_quote(python, exec_argument=True)} -m boti worker --config {unit_quote(config, exec_argument=True)}",
            f"Environment={unit_quote('PATH=' + path_env)}",
            "Environment=PYTHONUNBUFFERED=1",
            "Restart=always",
            "RestartSec=10",
            "TimeoutStopSec=30",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--at-boot", action="store_true", help="Enable lingering with sudo for startup before login"
    )
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this script on the Linux Mint worker")
    path = args.config.expanduser().resolve(strict=True)
    config = WorkerConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,60}", config.id):
        parser.error("Worker ID cannot be used as a service name")
    project = Path(__file__).resolve().parents[1]
    directory = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"boti-worker-{config.id}.service"
    unit = directory / name
    unit.write_text(service_text(sys.executable, project, path, os.environ.get("PATH", "/usr/bin:/bin")))
    unit.chmod(0o600)
    if args.at_boot:
        subprocess.run(["sudo", "loginctl", "enable-linger", getpass.getuser()], check=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", name], check=True)
    subprocess.run(["systemctl", "--user", "restart", name], check=True)
    subprocess.run(["systemctl", "--user", "is-active", name], check=True)
    print(f"Installed {name}")
    print(f"Logs: journalctl --user -u {name} -f")
    if not args.at_boot:
        print("Starts at login. Use --at-boot to also start before login.")


if __name__ == "__main__":
    main()
