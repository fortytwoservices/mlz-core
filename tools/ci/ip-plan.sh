#!/usr/bin/env bash
# ip-plan.sh: on a vending pull request, brings the IP plan and the names up to date and commits them to the
# pull request's branch, so nobody runs the allocator by hand. It allocates on the checkout, which is the pull
# request merged into its base: blocks the base has given out since the branch was made are taken into
# account, so two pull requests never get the same block (the check on main still stops one that does).
# A name is kept once it is on the base, where it can be deployed from; one this pull request gave on an
# earlier run is given again from its YAML as it is now, so a short name changed in review still counts.
#
# Exits 0 when both are already up to date, and 1 when it changed the branch or could not: this run then
# stops, and the run of the new commit goes on. Needs GH_TOKEN for an app that may write to this repo and
# update its pull requests, GITHUB_REPOSITORY, PR_NUMBER, HEAD_REF and HEAD_SHA (the pull request's branch
# and the commit this run checked), and BASE_SHA (its base).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
paths=(_generated/ip-plan.yaml _generated/names.yaml)
message="chore: IP plan and names for this pull request"

# The addresses come from the platform's contract, and the names from governance's: without both there is
# nothing to give out yet, and the check says vending waits for them.
for contract in platform governance; do
  if [[ ! -f _generated/$contract.yaml ]]; then
    echo "No $contract contract yet (_generated/$contract.yaml): no addresses or names to give out."
    exit 0
  fi
done

# The names on the base, which the allocator keeps. A base without the file has none yet; any other failure
# stops here, since giving every name again could rename what is deployed.
if ! gh api -H "Accept: application/vnd.github.raw" "repos/$GITHUB_REPOSITORY/contents/_generated/names.yaml?ref=$BASE_SHA" \
    > "$WORK/base-names.yaml" 2> "$WORK/base-names.err"; then
  if ! grep -q "HTTP 404" "$WORK/base-names.err"; then
    echo "::error::Could not read the base's names (_generated/names.yaml at ${BASE_SHA:0:7}): $(cat "$WORK/base-names.err")"
    exit 1
  fi
  : > "$WORK/base-names.yaml"
fi

# Writes the plan and the names, or nothing at all and fails when anything is wrong (a conflict, a landing
# zone that does not fit, a name Azure would refuse): that failure is the check's own, with every problem listed.
python3 -m mlz allocate --repo . --base-names "$WORK/base-names.yaml" >&2
if [[ -z $(git status --porcelain -- "${paths[@]}") ]]; then
  echo "The IP plan and the names are up to date."
  exit 0
fi
git diff -- "${paths[@]}" >&2 || true

# What this step committed must come out the same on the next run. If it does not, stop rather than commit
# again and again.
last=$(gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA" --jq .commit.message)
if [[ $last == "$message" ]]; then
  echo "::error::The IP plan or the names changed again after they were committed to this pull request. Run python3 -m mlz allocate --repo . on the branch and commit the result."
  exit 1
fi

# The plan written here includes what the base gave out since the branch was made. If the base changed the
# plan or the names in that time, committing them onto the branch as they are would conflict with the base,
# and a conflicting pull request starts no run. So the branch is first brought up to date with its base
# (GitHub's update branch, only if it is still at HEAD_SHA); the run of that merge then commits them on top.
moved=$(gh api "repos/$GITHUB_REPOSITORY/compare/$HEAD_SHA...$BASE_SHA" --jq '[.files[]?.filename] | any(. == "_generated/ip-plan.yaml" or . == "_generated/names.yaml")')
if [[ $moved == true ]]; then
  if ! gh api -X PUT "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/update-branch" -f expected_head_sha="$HEAD_SHA" >/dev/null; then
    echo "::error::The base changed the IP plan or the names since this branch was made, and the branch could not be brought up to date with it. Merge the base into the branch; the next run writes them."
    exit 1
  fi
  echo "::notice::The base changed the IP plan or the names since this branch was made, so the branch is now up to date with it. The run of that merge commits this pull request's."
  exit 1
fi

# One commit with both files, made only if the branch still points at HEAD_SHA: a push since this run started
# has a run of its own, which writes them for it.
python3 - "$GITHUB_REPOSITORY" "$HEAD_REF" "$HEAD_SHA" "$message" "${paths[@]}" > "$WORK/ip-plan-commit.json" <<'EOF'
import base64, json, sys
repository, branch, head, message, *paths = sys.argv[1:]
print(json.dumps({
    "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
    "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
        "expectedHeadOid": head,
        "message": {"headline": message},
        "fileChanges": {"additions": [{"path": path, "contents": base64.b64encode(open(path, "rb").read()).decode()} for path in paths]},
    }},
}))
EOF
if ! commit=$(gh api graphql --input "$WORK/ip-plan-commit.json" --jq .data.createCommitOnBranch.commit.oid); then
  now=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" --jq .head.sha)
  if [[ $now != "$HEAD_SHA" ]]; then
    echo "::notice::The branch moved on while this run allocated (now ${now:0:7}). The run of that commit writes the IP plan and the names."
  else
    echo "::error::Could not commit the IP plan and the names to $HEAD_REF."
  fi
  exit 1
fi
echo "::notice::The IP plan or the names were out of date, so they are now committed to this pull request (${commit:0:7}). The run of that commit plans with them."
exit 1
