"""Deliberately failing test: proof that CI blocks a merge on red.

Temporary. Deleted once the blocked PR has been observed.
"""


def test_ci_must_block_this_pr():
    assert 1 == 2, 'deliberate failure to prove CI blocks the merge'
