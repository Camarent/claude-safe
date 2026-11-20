---
name: git-and-release
description: Git identity, one-command-per-call, commit and merge rules, push and tag authority, gh authentication
metadata:
  type: feedback
---

- **Identity.** Every commit uses the GitHub noreply identity from the global git config. The owner's personal email must never appear in an author, message or committed file. Global hooks block it. If a commit is blocked or an identity is missing, report; never invent another identity. Before publishing a repo, check `git log --format=%ae | sort -u`.
- **One mutating git command per call** (add, commit, merge, switch). If the permission classifier blocks a chain or a batch, run the steps singly. This does not bypass an explicit denial: destructive or outward steps (force-push, history rewrite, branch deletion) still need the owner's word first.
- **Never commit on the default branch.** Check `git branch --show-current` first. Work goes on a stage or dev branch and reaches the default branch through a PR.
- **Commit finished stages** (review and tests passed, docs updated) without asking, with the session's Co-Authored-By trailer.
- **PRs.** Squash-merge a PR with green CI without asking (`gh pr merge N --squash --delete-branch`, then fast-forward local). Red CI: stop and report. Tags and releases need the owner's word.
- **Push the default branch after each merge** so the merge is not pointless, then republish whatever was built from it. Plain `git push` can fail for lack of credentials: use `git -c credential.helper='!gh auth git-credential' push origin <branch>`. Push explicit refs, never `--tags`.
- **`gh` is authenticated** (via `GH_TOKEN`). Run `gh auth status` before claiming credentials are missing.
- **Finish a stage by verifying, not by merging by hand:** after the driver exits 0, check `gh pr list --state all --head <stage>` and `git branch --list 'stage/*'` in every touched repo, and republish only if built content changed and the published copy is older than the merge.

**Why:** the owner was annoyed by false "no credentials" claims, personal-email leaks, and merges that were announced as pending after they were done.
**How to apply:** see [[orchestration]] for who may edit and merge.
