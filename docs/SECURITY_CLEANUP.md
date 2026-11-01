# Security cleanup: purge `config.json` from history

The `init` commit (`4df79e6`) added `config.json` with the real Google Sheet id of the
Personal Finance Tracker and the real Drive id of the `Personal Finance` root folder. This
repository is public. Both branches now drop the file and keep ids in the environment
(`DOEEDD_LEGACY_SHEET_ID`, `DOEEDD_DRIVE_ROOT_FOLDER_ID` on `feat/doeedd-integration`), but the
ids are still in every clone, fork and cached view of the old commits.

Treat the ids as already public. An id alone grants nothing while the Sheet and folder are
restricted, so step 1 (access) is what protects the data; the history rewrite stops the ids
spreading further.

Order:

1. Confirm the Sheet and Drive folder are private (now).
2. Merge the two cleanup pull requests. Before the integration branch reaches the Hermes VM,
   add the two ids to `~/.hermes/.env` there (see that branch's README), because the next
   pull deletes `config.json` from the VM checkout.
3. Purge history and force-push.
4. Re-clone everywhere.
5. Remove the gitleaks allowlist entry and ask GitHub Support to drop cached views.
6. Rotate only if step 1 found broader access than expected.

## 1. Access checklist

- [ ] Sheet: **Share** > General access is **Restricted** (not "Anyone with the link").
- [ ] Sheet: the people list holds only accounts you expect; remove the rest.
- [ ] Drive `Personal Finance` folder: **Share** > General access is **Restricted**; people list
      as expected.
- [ ] Sub-folders and files: sharing is per item and can be wider than the parent. List
      anything link-shared in the Drive API explorer (`files.list`) with
      `q = "visibility = 'anyoneWithLink' and trashed = false"` and
      `fields = "files(id, name, parents)"`. Restrict every hit under `Personal Finance`.
- [ ] Google Account > Security > **Your connections to third-party apps & services**: only the
      expected OAuth client (the Hermes `google-workspace` one) has Drive or Sheets access.
- [ ] Sheet: **File > Version history** shows no edits you do not recognise.
- [ ] Forks: `gh api repos/T0MYAMMM/finance-agent/forks --jq '.[].full_name'`. A fork keeps the
      old history and cannot be cleaned from here; if one exists, rotate (section 6).

If every box is ticked, rotation is optional.

## 2. Merge the cleanup pull requests

Merge `fix/secrets-baseline-main` into `main` and `fix/secrets-baseline-integration` into
`feat/doeedd-integration`, then set on the Hermes VM, in `~/.hermes/.env`:

```bash
DOEEDD_DRIVE_ROOT_FOLDER_ID=<drive-root-folder-id>
DOEEDD_LEGACY_SHEET_ID=<legacy-sheet-id>
```

Take the values from the VM's current `config.json` (`root_folder_id`, `spreadsheet_id`)
before you pull. Do not paste them into issues, pull requests or chat.

## 3. Purge history

Needs `git-filter-repo` 2.47 or newer (`pip install --user 'git-filter-repo>=2.47'`).
If `main` has branch protection that blocks force pushes, allow them for the duration.

Work in a fresh bare clone, never in a working checkout:

```bash
cd "$(mktemp -d)"
git clone --bare git@github.com:T0MYAMMM/finance-agent.git
cd finance-agent.git

# What is about to change (expect the init commit and the commit that deleted the file on main)
git log --all --oneline -- config.json

# Remove config.json from every commit on every ref
git filter-repo --sensitive-data-removal --invert-paths --path config.json

# Verify: both must print nothing
git log --all --oneline -- config.json
git grep -nE "${OLD_SHEET_ID}|${OLD_FOLDER_ID}" $(git rev-list --all)
```

Set `OLD_SHEET_ID` and `OLD_FOLDER_ID` in your shell first (`read -rs OLD_SHEET_ID` keeps them
out of shell history); an unset variable makes the grep match everything.

`filter-repo` prints the first changed commit(s) and how many pull requests reference old
commits. Keep that output for step 5. Then push:

```bash
git remote -v                     # filter-repo may drop origin; re-add it if so:
git remote add origin git@github.com:T0MYAMMM/finance-agent.git 2>/dev/null || true
git push --force --mirror origin
```

Rejections for `refs/pull/*` are expected: GitHub owns those refs. Branches and tags must
update. Open pull requests stay open and show the rewritten commits.

## 4. Re-clone everywhere

Every existing clone still has the old commits, and a `git pull` would merge them back.
Do not pull; re-clone, then copy over only untracked files you need (for example `.env`).

- [ ] Local checkout(s), including any worktrees: move the old directory aside, `git clone`,
      check out your branches, delete the old directory once nothing is missing.
- [ ] Hermes VM `~/finance-system`: same. Configuration and state live in `~/.hermes/`, not in
      the checkout, so nothing there needs copying.
- [ ] Any other machine or CI cache with a clone.

## 5. Finish

- [ ] Delete the `[[allowlists]]` block from `.gitleaks.toml` (the commit it names no longer
      exists) and commit. CI's secret scan must stay green without it.
- [ ] Optional local check from a fresh clone:
      `gitleaks git --redact --config .gitleaks.toml .` reports no leaks.
- [ ] Contact GitHub Support (<https://support.github.com/contact>, "Removing sensitive data")
      with the repository, the first changed commit(s) and affected pull requests from
      step 3. They remove cached views and the old `refs/pull/*` data.

## 6. Rotate (only if needed)

Rotation means moving the data to resources with new ids, then pointing the configuration
at them. Receipt file ids stay the same, so doeedd attachment links keep working.

- [ ] Drive: create a new folder (for example `Personal Finance 2`) with **Restricted**
      access. **Move** (do not copy) `Receipts/` and everything else from the old root into it.
      Moving keeps each file's id; copying would break existing receipt links.
- [ ] Check sharing on the moved items again (section 1); moving does not reset per-file
      sharing.
- [ ] Sheet: **File > Make a copy**, confirm the copy is **Restricted**, then move the old
      Sheet to the trash once nothing reads it. If the doeedd migration is finished, the old
      Sheet can simply be trashed after a download (**File > Download > .xlsx**) for your
      records.
- [ ] Update `~/.hermes/.env` on the VM with the new `DOEEDD_DRIVE_ROOT_FOLDER_ID` and
      `DOEEDD_LEGACY_SHEET_ID`, then run `doeedd.py health` and one receipt upload to confirm.
- [ ] Trash the empty old root folder.
