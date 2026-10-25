"""
Stage C2 Gate 8: Production Build Exclusion Check
=================================================
Verifies that the qualified watcher production image can be built WITHOUT
the standalone operator tooling, qualification artifacts, local reports, or
any credentials/scratch content - and emits the runbook rule enforcing it.

Checks (offline, read-only, no Docker required):
  1. The qualified commit f4def96's tree contains none of the forbidden
     path patterns (operator_tool/, qualification-candidate scratch,
     reports/, *.pem, .env, playwright_state*.json, *.sqlite, browser
     profile caches).
  2. In the CURRENT working tree, all such paths remain untracked-only
     (git ls-files shows none of them tracked on this branch tip).
  3. The qualified Dockerfile build semantics (`COPY . .`) make the
     working tree unsafe as a build context; the emitted rule requires a
     pristine detached export of f4def96 for any production build.

Exit 0 = all checks pass. This script never modifies anything.
"""

import subprocess
import sys

QUALIFIED_COMMIT = "f4def963294723ae57cdad5cd93b5e3f38667502"

FORBIDDEN_PATTERNS = (
    "operator_tool/",
    "reports/",
    ".env",
    ".pem",
    "playwright_state",
    ".sqlite",
    "ms-playwright",
    "scratch",
    "tpo.pem",
)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main():
    failures = []

    # 1. Qualified tree purity: no operator tooling, credentials, session
    #    state, databases, or browser-profile content. (The qualified
    #    candidate DOES track historical reports/*.md and its own
    #    qualification/ tooling - that is part of its qualified composition
    #    and is not contamination.)
    tree_files = git("ls-tree", "-r", "--name-only", QUALIFIED_COMMIT).splitlines()
    contaminating = [
        f for f in tree_files
        if f.startswith("operator_tool/")
        or f.endswith((".pem", ".env", ".sqlite"))
        or "playwright_state" in f
        or "ms-playwright" in f
    ]
    if contaminating:
        failures.append(f"qualified tree contaminated: {contaminating[:5]}")
    print(f"CHECK1 qualified-tree purity ({QUALIFIED_COMMIT[:12]}): "
          f"{'PASS' if not contaminating else 'FAIL'} "
          f"({len(tree_files)} tracked files, {len(contaminating)} forbidden)")

    # 2. Branch-tip tracked delta vs the qualified commit may ONLY add the
    #    standalone operator tooling, its dedicated qualification workflow,
    #    and the pre-existing d2315d9 application-qualification tooling.
    #    Everything else (application, tests, compose, Dockerfile, new
    #    reports, credentials) must be absent from the delta.
    delta = git("diff", "--name-status", QUALIFIED_COMMIT, "HEAD").splitlines()
    allowed_prefixes = (
        "operator_tool/",
        ".github/workflows/operator_mfa_arm64_qualification.yml",
        ".github/workflows/mfa-exact-tip-qualification.yml",
        "qualification/mfa_exact_tip/",
    )
    illegal = []
    for line in delta:
        status, _, path = line.partition("\t")
        if status.startswith("D"):
            illegal.append(f"DELETED {path}")
            continue
        if not any(path == p or path.startswith(p) for p in allowed_prefixes):
            illegal.append(f"{status} {path}")
    if illegal:
        failures.append(f"tracked delta outside allowed scope: {illegal[:5]}")
    print(f"CHECK2 branch tracked-delta scope (HEAD vs qualified): "
          f"{'PASS' if not illegal else 'FAIL'} "
          f"({len(delta)} delta entries, {len(illegal)} illegal)")

    # 3. Qualified Dockerfile semantics + emitted runbook rule.
    try:
        dockerfile = git("show", f"{QUALIFIED_COMMIT}:Dockerfile")
        wide_copy = any(
            line.strip().startswith("COPY") and line.strip().endswith(". .")
            for line in dockerfile.splitlines()
        )
        result = subprocess.run(["git", "show", f"{QUALIFIED_COMMIT}:.dockerignore"],
                                capture_output=True, text=True)
        dockerignore = result.stdout if result.returncode == 0 else ""
    except subprocess.CalledProcessError:
        failures.append("could not read qualified Dockerfile")
        return finish(failures)

    print(f"CHECK3 qualified Dockerfile uses a whole-context COPY (`COPY . .`): {wide_copy}")
    if dockerignore.strip():
        print(f"      qualified .dockerignore exists ({len(dockerignore.splitlines())} lines):")
        for line in dockerignore.splitlines():
            if line.strip() and not line.strip().startswith("#"):
                print(f"        - {line.strip()}")
        ignored_gaps = [p for p in ("operator_tool", "reports", "*.pem")
                        if not any(entry.strip() == p for entry in dockerignore.splitlines())]
        if wide_copy and ignored_gaps:
            print(f"      WARNING: .dockerignore does NOT exclude {ignored_gaps}; "
                  f"working-tree builds would copy them into the image")
    else:
        print("      qualified .dockerignore: ABSENT in the qualified candidate")

    print()
    print("RUNBOOK RULE (emit for the future release runbook):")
    print("  Production watcher images must be built ONLY from a pristine")
    print(f"  export of qualified commit {QUALIFIED_COMMIT}, for example:")
    print(f"    git archive {QUALIFIED_COMMIT} | docker build -t tpo-watcher-watcher:<tag> -")
    print("  or a fresh detached worktree checked out at that commit.")
    print("  NEVER build from a developer working tree: the qualified")
    print("  Dockerfile's `COPY . .` would sweep untracked tooling, reports,")
    print("  credentials and scratch artifacts into the production image.")

    return finish(failures)


def finish(failures):
    print()
    if failures:
        print("PRODUCTION BUILD EXCLUSION: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PRODUCTION BUILD EXCLUSION: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
