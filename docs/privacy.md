# Repository privacy

Public attribution uses the GitHub handle in `LICENSE`. The license terms and
third-party notices remain in place. Documentation paths use synthetic home
directories; README links are relative to this repository. Example people and
photographer labels are synthetic.

## Future commits

Use the GitHub-provided noreply address from Settings > Emails for both author
and committer. Check `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`
before publishing. Global Git defaults apply only on this computer, and local
repository settings or environment variables can override them. Configure other
computers and development environments separately.

In [GitHub email settings](https://github.com/settings/emails), enable both
**Keep my email addresses private** and **Block command line pushes that expose
my email**. These account settings were not changed or verified by this cleanup.

## Existing exposure

The October 26, 2026 cleanup sanitizes the current Image Triage files. It does
not erase previous versions, commit author/committer addresses, release assets,
other branches or tags, forks, cached pages, or copies downloaded by others.
Stillwave and WonderLanternZ7 also have published commits using a personal email;
their histories were inspected but have not been changed.

Removing the historical copies requires a separate, explicitly authorized
history rewrite. Before doing so, back up each affected repository outside its
published refs, inventory every branch and tag, stop concurrent pushes, and
review open pull requests. Sanitize historical file contents and both author
and committer metadata while preserving other contributors' attribution. Check
all rewritten refs before replacing published refs with leases against their
recorded heads. Existing clones must then be replaced or carefully cleaned so
they cannot reintroduce the old history. Review release binaries and archives
separately. Cached commits or third-party copies may remain accessible.

See [GitHub's history cleanup documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

Changing a username does not erase the old associations. External profiles,
search results, and reused handles need separate account reviews.

## Local research data

Grouping-spike metadata uses `photographer_a` and `photographer_b`. The round-two
extractor defaults to recognizing folders containing `photographer_b` as the
secondary photographer. For an existing private folder layout, set
`IMAGE_TRIAGE_SECONDARY_PHOTOGRAPHER_MARKER` locally to its distinguishing folder
text. Do not commit that private value. Regenerate older local feature caches
before analyzing them with the anonymous labels.
