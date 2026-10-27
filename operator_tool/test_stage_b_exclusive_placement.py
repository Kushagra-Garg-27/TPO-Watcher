"""
Stage C2: Offline Stage B Placement Mechanism Verification
==========================================================
Rehearses the corrected exclusive-placement runbook scripts against a
sandboxed directory with stubbed git/docker/python3, producing receipts for:

  1. Clean success: exclusive placement, exact bytes, no temp residue.
  2. Collision: destination pre-exists -> reliable nonzero exit, original
     bytes untouched, temp cleaned (the mv -n defect, corrected).
  3. Validation failure: no override left behind, temp cleaned.
  4. Hash-mismatch rollback: placed file removed when post-hash differs.
  5. Hash-guarded rollback: refuses to remove a foreign/edited file.
  6. mv -n defect receipt: GNU mv -n exits 0 on collision (documented).
  7. ln/os.link EEXIST receipt: the exclusive primitive fails loudly.

Platform honesty: production target is Amazon Linux 2023 (GNU coreutils);
this Windows Git Bash rehearsal proves the script logic, not AL2023 itself.
No SSH, no EC2 contact, no production mutation of any kind.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLACE = os.path.join(HERE, "stage_b_exclusive_place.sh")
ROLLBACK = os.path.join(HERE, "stage_b_hash_guarded_rollback.sh")

OVERRIDE_BYTES = (
    "services:\n"
    "  watcher:\n"
    "    environment:\n"
    "      - STATE_FILE=/app/data/session/playwright_state.json\n"
).encode("utf-8")
APPROVED_SHA = hashlib.sha256(OVERRIDE_BYTES).hexdigest()

VALID_COMPOSE_JSON = (
    '{"services": {"watcher": {"environment": '
    '{"STATE_FILE": "/app/data/session/playwright_state.json", '
    '"DB_PATH": "/app/data/watcher.sqlite"}}, "caddy": {}}}'
)
HEAD = "053b152b37d8aea8d5b4c74606765cdc73565136"

passed = 0
failures = []


def check(label, condition, detail=""):
    global passed
    if condition:
        passed += 1
        print(f"PASS {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"FAIL {label}: {detail}")


def make_stub_bin(directory, mode: str):
    """Creates stub git/docker/python3 executables for the sandbox."""
    bin_dir = os.path.join(directory, "stubbin")
    os.makedirs(bin_dir, exist_ok=True)

    git_sh = f"""#!/bin/sh
if [ "$1" = "rev-parse" ]; then echo "{HEAD}"; exit 0; fi
exit 1
"""
    docker_sh = f"""#!/bin/sh
case "$*" in
    *inspect*)
        echo "running"; exit 0 ;;
    *config*)
        if [ "{mode}" = "bad-config" ]; then echo "not json at all"; exit 0; fi
        if [ "{mode}" = "wrong-state-file" ]; then
            echo '{{"services": {{"watcher": {{"environment": {{"STATE_FILE": "/wrong/path"}}}}}}}}'
            exit 0
        fi
        echo '{VALID_COMPOSE_JSON}'
        exit 0 ;;
esac
exit 0
"""
    python3_sh = f"#!/bin/sh\nexec {sys.executable} \"$@\"\n"

    for name, content in (("git", git_sh), ("docker", docker_sh), ("python3", python3_sh)):
        path = os.path.join(bin_dir, name)
        with open(path, "w", newline="\n") as f:
            f.write(content)
        os.chmod(path, 0o755)
    return bin_dir


def run_script(script, sandbox, mode="ok", env_extra=None):
    bin_dir = make_stub_bin(sandbox, mode)
    base_compose = os.path.join(sandbox, "docker-compose.yml")
    with open(base_compose, "w", newline="\n") as f:
        f.write("services: {watcher: {}, caddy: {}}\n")
    env = dict(os.environ)
    env["PATH"] = bin_dir + os.pathsep + env["PATH"]
    # Forward-slash form: GNU sha256sum prefixes digests with a backslash
    # when the FILE NAME contains backslashes (Windows-only artifact; the
    # AL2023 production path /home/ec2-user/TPO-Watcher has none).
    env["STAGE_B_TARGET_DIR"] = sandbox.replace(os.sep, "/")
    env["STAGE_B_OVERRIDE_SHA256"] = APPROVED_SHA
    env["STUB_MODE"] = mode
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["bash", script], capture_output=True, text=True, env=env, timeout=120,
        cwd=sandbox,
    )
    return proc


def temp_residue(sandbox):
    return [f for f in os.listdir(sandbox) if ".docker-compose.override." in f]


def main():
    sandbox_root = tempfile.mkdtemp(prefix="tpo_c2_stageb_")
    try:
        # 1. Clean success.
        s1 = os.path.join(sandbox_root, "s1")
        os.makedirs(s1)
        proc = run_script(PLACE, s1)
        placed = os.path.join(s1, "docker-compose.override.yml")
        success = (
            proc.returncode == 0
            and os.path.exists(placed)
            and open(placed, "rb").read() == OVERRIDE_BYTES
            and not temp_residue(s1)
        )
        check("01 [SCRIPT] Clean placement: exclusive, exact bytes, no residue",
              success, f"rc={proc.returncode} out={proc.stdout.strip()[:120]} err={proc.stderr.strip()[:120]}")

        # 2. Collision: destination already exists.
        s2 = os.path.join(sandbox_root, "s2")
        os.makedirs(s2)
        existing = os.path.join(s2, "docker-compose.override.yml")
        foreign_bytes = b"services: {watcher: {environment: [{STATE_FILE: /foreign/operator.yml}]}}\n"
        with open(existing, "wb") as f:
            f.write(foreign_bytes)
        proc = run_script(PLACE, s2)
        collision_ok = (
            proc.returncode == 11
            and open(existing, "rb").read() == foreign_bytes
            and not temp_residue(s2)
        )
        check("02 [SCRIPT] Pre-existing destination: reliable nonzero exit, bytes untouched, no residue",
              collision_ok, f"rc={proc.returncode}")

        # 3. Validation failure (unparseable effective config).
        s3 = os.path.join(sandbox_root, "s3")
        os.makedirs(s3)
        proc = run_script(PLACE, s3, mode="bad-config")
        no_override = not os.path.exists(os.path.join(s3, "docker-compose.override.yml"))
        check("03 [SCRIPT] Validation failure: nothing placed, temp cleaned",
              proc.returncode in (13, 14) and no_override and not temp_residue(s3),
              f"rc={proc.returncode}")

        # 3b. Validation failure (wrong STATE_FILE allowlist value).
        s3b = os.path.join(sandbox_root, "s3b")
        os.makedirs(s3b)
        proc = run_script(PLACE, s3b, mode="wrong-state-file")
        no_override_b = not os.path.exists(os.path.join(s3b, "docker-compose.override.yml"))
        check("03b [SCRIPT] Allowlist violation rejected: nothing placed",
              proc.returncode in (13, 14) and no_override_b and not temp_residue(s3b),
              f"rc={proc.returncode}")

        # 4. Approved-hash mismatch at staging time.
        s4 = os.path.join(sandbox_root, "s4")
        os.makedirs(s4)
        proc = run_script(PLACE, s4, env_extra={"STAGE_B_OVERRIDE_SHA256": "0" * 64})
        check("04 [SCRIPT] Staged hash mismatch aborts placement",
              proc.returncode == 12
              and not os.path.exists(os.path.join(s4, "docker-compose.override.yml"))
              and not temp_residue(s4), f"rc={proc.returncode}")

        # 5. Hash-guarded rollback removes only the exact Stage B file.
        s5 = os.path.join(sandbox_root, "s5")
        os.makedirs(s5)
        rollback_target = os.path.join(s5, "docker-compose.override.yml")
        with open(rollback_target, "wb") as f:
            f.write(OVERRIDE_BYTES)
        proc = run_script(ROLLBACK, s5)
        check("05 [SCRIPT] Hash-guarded rollback removes the exact approved file",
              proc.returncode == 0 and not os.path.exists(rollback_target), f"rc={proc.returncode}")

        # 5b. Rollback keeps a foreign file.
        s5b = os.path.join(sandbox_root, "s5b")
        os.makedirs(s5b)
        foreign = os.path.join(s5b, "docker-compose.override.yml")
        with open(foreign, "wb") as f:
            f.write(foreign_bytes)
        proc = run_script(ROLLBACK, s5b)
        check("05b [SCRIPT] Rollback refuses to remove non-approved content",
              proc.returncode == 21 and open(foreign, "rb").read() == foreign_bytes,
              f"rc={proc.returncode}")

        # 6. mv -n defect receipt (GNU coreutils: exit 0 while placing nothing).
        probe = os.path.join(sandbox_root, "mvn_probe")
        os.makedirs(probe)
        src, dst = os.path.join(probe, "src"), os.path.join(probe, "dst")
        with open(src, "w") as f:
            f.write("new")
        with open(dst, "w") as f:
            f.write("original")
        mvn = subprocess.run(["bash", "-c", "mv -n src dst; echo $?"],
                             capture_output=True, text=True, cwd=probe)
        mvn_exit = int(mvn.stdout.strip().splitlines()[-1])
        mvn_dst_untouched = open(dst).read() == "original" and os.path.exists(src)
        check("06 [COREUTILS] mv -n defect receipt: exit 0 on collision, nothing placed",
              mvn_exit == 0 and mvn_dst_untouched, f"exit={mvn_exit}")

        # 7. ln/os.link exclusive primitive receipt.
        link_src, link_dst = os.path.join(probe, "link_src"), os.path.join(probe, "link_dst")
        with open(link_src, "w") as f:
            f.write("new")
        with open(link_dst, "w") as f:
            f.write("original")
        ln = subprocess.run(["bash", "-c", "ln link_src link_dst 2>/dev/null; echo $?"],
                            capture_output=True, text=True, cwd=probe)
        ln_exit = int(ln.stdout.strip().splitlines()[-1])
        ln_dst_untouched = open(link_dst).read() == "original"
        py_link_failed = False
        try:
            os.link(link_src, link_dst)
        except FileExistsError:
            py_link_failed = True
        check("07 [COREUTILS] ln/os.link primitive fails loudly on collision",
              ln_exit != 0 and ln_dst_untouched and py_link_failed, f"ln_exit={ln_exit}")
    finally:
        shutil.rmtree(sandbox_root, ignore_errors=True)

    total = passed + len(failures)
    print()
    if failures:
        for failure in failures:
            print(f"  - {failure}")
    print(f"STAGE B PLACEMENT MECHANISM RESULT: {passed}/{total} PASSED"
          + ("" if not failures else f", {len(failures)} FAILED"))
    return not failures


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
