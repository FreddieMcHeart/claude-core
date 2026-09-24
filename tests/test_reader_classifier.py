"""classify_reader_call: which reader family, if any, a Bash command's read belongs to.

The detectors this replaces were written for a warning, where a false positive cost nothing.
Under a block, a false positive refuses a write and a miss lets a read through, so both
directions are asserted here, row by row, from the design's case table.
"""
import importlib.util
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
_spec = importlib.util.spec_from_file_location("cost_discipline_classifier", MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)

READS = [
    ("kubectl get pods", "kubectl"),
    ("kubectl --context=x -n ns get pods", "kubectl"),
    ("kubectl --context x -n ns logs pod/y", "kubectl"),
    ("gh pr view 5", "gh"),
    ("/opt/homebrew/bin/gh pr checks 5", "gh"),
    ("cd ~/repo && gh pr list", "gh"),
    ("GH_REPO=o/r gh pr list", "gh"),
    ("env -u TERM gh pr list", "gh"),
    ("gh -R o/r pr view 5", "gh"),
    ("gh --repo o/r pr list", "gh"),
    ("gh search code foo --owner o", "gh"),
    ("gh pr list | head -5", "gh"),
    ("gh pr list 2>&1 | head -5", "gh"),
    ("cd repo\ngh pr list", "gh"),
    ("(cd x && gh pr list)", "gh"),
    ("gh api repos/o/r/pulls", "gh"),
    ("gh api graphql -f query='{ viewer { login } }'", "gh"),
    ("~/.claude/skills/x/pup-ro.sh metrics query q", "pup"),
    ("~/.claude/scripts/slack-cli.sh history C123", "slack"),
    ("gcloud projects list", "gcloud"),
    ("gcloud config get-value project", "gcloud"),
    ("gcloud logging logs list", "gcloud"),
    ("gcloud services list", "gcloud"),
    ("gcloud projects describe p", "gcloud"),
    ("gcloud logging read 'severity>=ERROR' --limit 5", "gcloud"),
    ("vault status", "vault"),
    ("vault kv list secret/", "vault"),
    ("vault -address=https://x secrets list", "vault"),
    # an unlisted flag before the verb: its value is set aside, the real verb still reads
    ("gcloud --verbosity debug projects list", "gcloud"),
    ("kubectl get pods -l app=x", "kubectl"),
]

NOT_READS = [
    "gh pr create --title x",
    "gh pr view 5 && gh pr merge 5",
    "gh api -X POST repos/o/r/issues",
    "gh api -X DELETE repos/o/r/x",
    "gh api repos/o/r/issues/1/comments -f body=x",
    "gh api graphql -f query='mutation{addStar(input:{}){clientMutationId}}'",
    "kubectl apply -f x.yaml",
    "kubectl delete configmap top",
    "kubectl rollout restart deploy/x && kubectl get pods",
    'git commit -m "docs: kubectl get pods"',
    "echo 'kubectl logs x'",
    "cat > f.md <<'EOF'\nkubectl get pods\nEOF",
    "git commit -m wip && gh pr list",
    # multi-line: each line is a command, a continuation is not a new one
    "kubectl get pods -n x\nkubectl delete pod y -n x",
    "gh api repos/o/r/issues \\\n  -f title=x",
    "gh api repos/o/r/issues \\\n  -X POST",
    'git commit -m "fix\nkubectl get pods\n"',
    "vault write secret/x a=b",
    "vault read secret/x",
    "vault kv get secret/x",
    "gcloud run deploy x",
    "gcloud services enable x",
    "gcloud --help",
    "gcloud services list --help",
    "gcloud container clusters get-credentials c --region r",
    "~/.claude/scripts/slack-cli.sh send C1 hi",
    # review 2026-09-24: the value of an unlisted flag must never be taken for the read verb
    "kubectl --field-selector logs delete pod mypod",
    "kubectl -l get delete pods",
    "gcloud --filter list compute instances delete vm1 --zone=us-central1-a",
    "gcloud compute instances --quiet delete list",
    "vault -tls-server-name list write secret/x a=b",
    "~/.claude/scripts/slack-cli.sh --workspace history send C1 hi",
    "git status",
    "echo 'unbalanced",
    "",
]


@pytest.mark.parametrize("cmd,family", READS)
def test_read_is_classified_to_its_family(cmd, family):
    assert cd.classify_reader_call(cmd) == family


@pytest.mark.parametrize("cmd", NOT_READS)
def test_non_read_is_not_classified(cmd):
    assert cd.classify_reader_call(cmd) is None


def test_every_family_maps_to_a_reader_name_that_exists_as_a_convention():
    """pup is served by datadog-reader, so the name is looked up, never derived."""
    assert cd.READER_FOR_FAMILY["pup"] == "datadog-reader"
    assert set(cd.READER_FOR_FAMILY) == {"kubectl", "gh", "pup", "slack", "gcloud", "vault"}
    assert all(cd._READER_NAME_RE.fullmatch(v) for v in cd.READER_FOR_FAMILY.values())


def test_every_family_has_a_fallback_warning():
    assert set(cd.READER_FOR_FAMILY) <= set(cd.READER_REFLEX)
