"""The repository's own configuration is part of the security posture.

Nothing here imports the application. These tests pin the shipped infrastructure
files, because the failure modes they guard against do not show up in any
application test:

* A compose file that publishes PostgreSQL on ``0.0.0.0`` hands the database to
  whatever network the developer's laptop is attached to.
* A workflow without a ``permissions`` block gets GitHub's permissive default
  token, so any compromised third-party action can push to the repository.
* A CI pipeline that quietly loses its secret scan still shows a green tick.

Each is a one-line edit away, and none of them breaks a feature — which is
exactly why they need a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))


def _load(relative: str) -> dict:
    return yaml.safe_load((_ROOT / relative).read_text(encoding="utf-8"))


def _compose_services() -> dict[str, dict]:
    return _load("docker-compose.yml")["services"]


# --- Compose: backing services must not be exposed off-host -----------------


@pytest.mark.parametrize("service", sorted(_compose_services()))
def test_no_compose_service_is_published_on_a_wildcard_interface(service):
    """``"5432:5432"`` binds every interface; ``"127.0.0.1:5432:5432"`` does not.

    The development database and Redis have weak, well-known credentials. Bound
    to a wildcard address they are reachable by anyone sharing a cafe or hotel
    network with the machine running the stack.
    """
    for published in _compose_services()[service].get("ports", []):
        assert str(published).startswith("127.0.0.1:"), (
            f"service '{service}' publishes {published!r} on all interfaces; "
            f"prefix it with 127.0.0.1: to keep it on the loopback interface"
        )


def test_compose_tolerates_a_missing_env_file():
    """A fresh clone must be able to `docker compose up` before writing .env.

    The bare ``env_file: .env`` form is a hard error when the file is absent,
    which turns the documented first run into a failure.
    """
    for name, service in _compose_services().items():
        for entry in service.get("env_file", []):
            assert isinstance(entry, dict) and entry.get("required") is False, (
                f"service '{name}' requires {entry!r} to exist; "
                f"use the mapping form with required: false"
            )


def test_compose_never_hardcodes_a_credential_without_an_override():
    """Development defaults are fine; an unoverridable one is not."""
    db_env = _compose_services()["db"]["environment"]
    assert db_env["POSTGRES_PASSWORD"].startswith("${"), (
        "the compose database password must be overridable from the environment"
    )


# --- Workflows: least-privilege tokens --------------------------------------


def test_there_is_at_least_one_workflow():
    """Guards the parametrised tests below from silently covering nothing."""
    assert _WORKFLOWS


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_declares_least_privilege_permissions(workflow):
    """Without a ``permissions`` block the job inherits a broad write token.

    That token is available to every action in the job, including transitive
    dependencies of third-party actions, and can push commits and releases.
    """
    config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    permissions = config.get("permissions")
    assert permissions is not None, f"{workflow.name} does not restrict its token"
    assert permissions.get("contents") == "read", (
        f"{workflow.name} should grant only contents: read at the top level; "
        f"a job that needs more must opt in for itself"
    )


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_grants_write_access_at_the_top_level(workflow):
    config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    for scope, level in config["permissions"].items():
        assert level == "read", (
            f"{workflow.name} grants '{scope}: {level}' to every job; "
            f"move it onto the single job that needs it"
        )


# --- CI: the security gates are actually wired up ---------------------------


def _ci_jobs() -> dict[str, dict]:
    return _load(".github/workflows/ci.yml")["jobs"]


@pytest.mark.parametrize("job", ["quality", "secrets", "audit"])
def test_ci_runs_the_security_gates(job):
    """Deleting one of these leaves a green tick and no coverage of that risk."""
    assert job in _ci_jobs(), f"the '{job}' job has been removed from CI"


def test_the_secret_scan_covers_the_whole_history_and_redacts_findings():
    """Two easy mistakes, both of which make the scan worthless.

    A shallow checkout scans only the tip, missing a secret that was committed
    and later deleted — which is still in the pack files, and still compromised.
    Dropping ``--redact`` makes the build log a second public copy of whatever
    the scan just found.
    """
    steps = _ci_jobs()["secrets"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the secret scan needs the full history, not a shallow checkout"
    )

    scans = [str(s.get("run", "")) for s in steps if "gitleaks" in str(s.get("run", ""))]
    assert any("gitleaks git" in cmd for cmd in scans), "no gitleaks scan of the commit history"
    assert any("gitleaks dir" in cmd for cmd in scans), "no gitleaks scan of the working tree"
    for cmd in scans:
        if "gitleaks git" in cmd or "gitleaks dir" in cmd:
            assert "--redact" in cmd, "a gitleaks scan must redact matches from the build log"


def test_the_dependency_audit_is_strict():
    """Without --strict, pip-audit exits 0 when a package cannot be checked."""
    runs = " ".join(str(s.get("run", "")) for s in _ci_jobs()["audit"]["steps"])
    assert "pip-audit --strict" in runs


def test_the_docker_build_waits_for_every_gate():
    """A publishable image must not be built from code that failed a check."""
    assert set(_ci_jobs()["docker"]["needs"]) >= {"quality", "secrets", "audit"}


def test_no_workflow_hardcodes_a_secret():
    """CI generates its keys per run; a literal here would be committed forever."""
    for workflow in _WORKFLOWS:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            for setting in ("MASTER_ENCRYPTION_KEY", "JWT_SECRET_KEY"):
                if not stripped.startswith((f"export {setting}", f"{setting}:")):
                    continue
                assert "$(" in stripped or "${{" in stripped, (
                    f"{workflow.name} appears to assign a literal {setting}"
                )


# --- Disclosure and contribution policy -------------------------------------


@pytest.mark.parametrize(
    ("filename", "must_mention"),
    [
        ("SECURITY.md", "security/advisories/new"),
        ("CONTRIBUTING.md", "SECURITY.md"),
        (".github/PULL_REQUEST_TEMPLATE.md", "No secret is committed"),
    ],
)
def test_policy_documents_are_present_and_point_somewhere_useful(filename, must_mention):
    """A disclosure policy nobody can find results in a public 0-day issue."""
    text = (_ROOT / filename).read_text(encoding="utf-8")
    assert must_mention in text, f"{filename} no longer mentions {must_mention!r}"


def test_the_bug_report_template_warns_against_pasting_credentials():
    """The likeliest leak in a public repo is a user pasting their own .env."""
    text = (_ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml").read_text(encoding="utf-8")
    assert "Never paste an API key" in text


def test_blank_issues_are_disabled_so_the_security_link_is_seen():
    """The template chooser is where a reporter is told not to file publicly."""
    assert _load(".github/ISSUE_TEMPLATE/config.yml")["blank_issues_enabled"] is False


# --- gitignore: the last line of defence ------------------------------------


@pytest.mark.parametrize("pattern", [".env", ".env.*", "!.env.example", "*.pem", "*.key"])
def test_gitignore_still_covers_secret_material(pattern):
    """These five lines are why a stray key never becomes a commit."""
    lines = {ln.strip() for ln in (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert pattern in lines, f".gitignore no longer ignores {pattern}"


def test_the_gitleaks_config_keeps_the_upstream_rules():
    """`useDefault = false` would silently reduce the scan to the allowlist.

    The config exists only to record reviewed false positives. Turning off the
    upstream rule set would leave a scan that passes because it looks for
    nothing, which is worse than having no scan at all.
    """
    import tomllib

    config = tomllib.loads((_ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True

    # An allowlist entry is a reviewed exception, not a way to mute the scanner.
    # Keeping the list short forces each new one to be argued for.
    assert len(config["allowlist"]["regexes"]) <= 8


def test_ci_uses_the_reviewed_gitleaks_config():
    """A scan run without --config silently ignores the reviewed exceptions."""
    for step in _ci_jobs()["secrets"]["steps"]:
        command = str(step.get("run", ""))
        if "gitleaks git" in command or "gitleaks dir" in command:
            assert "--config .gitleaks.toml" in command


def test_dependabot_watches_every_ecosystem_that_ships_code():
    """Pip alone leaves pinned actions and base images to rot."""
    ecosystems = {u["package-ecosystem"] for u in _load(".github/dependabot.yml")["updates"]}
    assert {"pip", "github-actions", "docker"} <= ecosystems
