"""Names of the merge step's CI wait: how long a PR step waits for pending checks, how often it reads them, and what gh answers while they are pending."""

CHECKS_TIMEOUT_S = 1800  # pr_checks_timeout default: seconds a PR step waits for pending CI (and for a mergeability still UNKNOWN)
CI_POLL_S = 10  # seconds between two reads of the workflow runs of a PR head (the checks of the merge gate)
GH_CHECKS_PENDING_RC = 8  # return code of _pr_checks while a workflow run is still pending (was the exit code of `gh pr checks`)
