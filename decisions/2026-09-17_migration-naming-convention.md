# All migrations renamed to `YYYYMMDD_slug.sql`

**Date:** 2026-09-17
**Status:** Accepted.

## Context

`migrations/` had accumulated 39 files under three incompatible conventions:

| Convention | Count | Example |
|---|---|---|
| Undated verb-first | 37 | `add_trigger_history.sql` |
| ISO with dashes | 1 | `2026-08-13_apply_missing_migrations.sql` |
| Compact date | 1 | `20260624_twr_schema.sql` |

With 37 of 39 undated, the directory sorted **alphabetically by verb** — every
`add_*` clustered together regardless of age, with `create_*`, `drop_*`, `fix_*`
and `patch_*` scattered around them. The listing carried no information about
sequence at all.

That is a real problem in this repo rather than a cosmetic one, because **nothing
runs these migrations**. There is no runner, no `schema_migrations` table, no
ordering metadata. They are applied by hand in the Supabase SQL Editor, so the
filename is the *only* record of sequence that exists.

The cost of that is already in the tree:
`2026-08-13_apply_missing_migrations.sql` is a consolidated recovery patch whose
own header explains it exists because migrations had gone unapplied and the live
schema had drifted from the repo. Its two-convention-breaking siblings are the
two files someone dated *by hand* precisely when sequence mattered most.

## Decision

Rename every migration to `YYYYMMDD_slug.sql`, and add a mandatory rule to
`AGENTS.md` so new ones follow automatically.

`YYYYMMDD` is chosen over `YYYY-MM-DD` because it is the only common date format
where **lexical order equals chronological order** while also aligning to a fixed
width. That single property makes `ls`, `ls -r`, a file-tree pane and
`git diff --stat` all agree on chronology with no tooling.

### How each date was derived

- **Self-declared date wins where one exists.** `2026-08-13_apply_missing_migrations.sql`
  became `20260813_…` even though git records it as *added* on 08-15, because its
  header reads "Audited: 2026-08-13" and it is paired with
  `decisions/2026-08-13_reconcile-supabase-schema-drift.md`. Preserving the
  self-declared date keeps that link intact.
- **`20260624_twr_schema.sql` was left untouched** — already conformant.
- **Everything else took its git add-date**
  (`git log --diff-filter=A --format=%ad --date=format:%Y%m%d -1 -- <file>`),
  which is the most reliable available evidence of when the migration was written.

Same-day migrations sort alphabetically within the day. No fake times or sequence
numbers were invented to break ties; 12 files share a date with at least one
other and that is harmless.

## Consequences

- 38 files renamed via `git mv`, so history follows them. Zero collisions.
- **258 references across 62 files** were rewritten. The filename is *not* an
  internal detail: `schema_guard.py` prints migration filenames to the operator
  in its repair messages and hard-codes `REPAIR_SCRIPT`, and the migrations are
  cited throughout `docs/` and `decisions/`.
- References inside `decisions/` were updated too. An ADR that names a file which
  no longer exists is a broken instruction, not a preserved record — this is a
  path correction, not a rewrite of any decision, so the "never edit an ADR body"
  rule is not engaged.
- `tests/test_supabase_backup.py` globs `migrations/*.sql` but parses SQL
  *content* for table names, so it was unaffected. Full suite: **666 passed**.
- Nothing about the applied state of the live Supabase schema changes. This is a
  filename change only; no migration was edited, added or removed.

## Notes

The rename used a negative lookbehind (`(?<!\d{8}_)`) when rewriting references,
so an already-dated name could not be prefixed twice — the obvious failure mode
of a bulk find-and-replace like this. Verified afterwards: zero stale references
and zero double prefixes.

`.graphify_ast.json` and `.graphify_detect.json` were incidentally caught by the
first pass and reverted; they are generated artifacts and were rebuilt by
`graphify update .`.
