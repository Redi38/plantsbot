#!/usr/bin/env python3
import os
import pwd
import sys

DATA_DIR = "/app/data"
RUN_AS_USER = "appuser"


def _chown_tree(path: str, uid: int, gid: int) -> None:
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.chown(os.path.join(root, name), uid, gid)
    os.chown(path, uid, gid)


def main() -> None:
    if os.getuid() == 0:
        pw = pwd.getpwnam(RUN_AS_USER)
        os.makedirs(DATA_DIR, exist_ok=True)
        _chown_tree(DATA_DIR, pw.pw_uid, pw.pw_gid)

        os.setgid(pw.pw_gid)
        os.initgroups(RUN_AS_USER, pw.pw_gid)
        os.setuid(pw.pw_uid)

    if len(sys.argv) < 2:
        sys.exit("docker-entrypoint.py: нет команды для запуска")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
