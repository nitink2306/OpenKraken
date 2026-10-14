# Maintaining the Kraken Plus fork

This fork preserves the tested Plus fix while retaining the original project's
history and license.

- `origin`: the personal fork, `https://github.com/nitink2306/OpenKraken`.
- `upstream`: the original project, `https://github.com/davidboulay/OpenKraken`.
- `main`: reserved for upstream code; do not put personal fixes here.
- `codex/kraken-plus-support`: the maintained branch with the Plus fix, tests,
  and investigation results. Run this branch on the cooler.

The initial fix is based on upstream release v0.4.1, commit `c637ca8`.
GitHub preserves committed source and test evidence; it does not install the
patched app, save local fan preferences, or change the desktop/autostart launcher.
Back up `~/.config/openkraken/` separately. Do not publish personal config or raw
USB debug logs as part of routine updates.

## Bring in upstream updates

Use a temporary worktree so reviewing updates does not alter the checkout used
by the running application. Start with a clean working tree. Give each update
branch and directory a new name if the example names already exist.

```sh
git fetch upstream
git fetch origin
git worktree add -b codex/plus-update ../OpenKraken-update codex/kraken-plus-support
cd ../OpenKraken-update
git merge upstream/main
```

Resolve any conflicts by retaining the Plus behavior and accommodating upstream
changes. Inspect changes in both OpenKraken and liquidctl; this adapter depends on
private liquidctl helpers. If upstream provides equivalent Plus support, replace
the local adapter only after testing it on the device.

In the project's Python environment with its runtime dependencies installed:

```sh
python -m unittest discover -s tests
git diff --check
git push -u origin codex/plus-update
```

Review the candidate, then close the running application before testing the
candidate with the cooler. Never run two instances against the same device.
Verify static/live LCD, temperature readings, pump duty, and liquid fan control.
After a successful hardware check, close the candidate application and promote
its commits from the original checkout:

```sh
cd ../OpenKraken
git switch codex/kraken-plus-support
git merge --ff-only codex/plus-update
git push origin codex/kraken-plus-support
python -m openkraken
```

If the fast-forward fails, the maintained branch advanced during testing: merge
its changes into the candidate and test again. Keep merge history; no force-push
is required. The clean `main` branch can be updated separately with GitHub's
Sync fork action.

Updates are deliberately reviewed and tested before promotion. No scheduled
upstream updater or automatic deployment is configured. To request an update,
ask to merge the latest upstream changes into the Plus-support branch and verify
them. A future contribution to the original project can reduce the amount of
custom code this fork needs to carry.
