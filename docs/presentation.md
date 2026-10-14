# The presentation contract

One contract for both tools. Dotfiles and Agentbot are used in the same
terminal, often in the same run — `dotfiles full-update` prints output from
both — so a reader should not have to learn two designs.

It records what the tests already enforce rather than an intention, so the
two products cannot drift apart silently. Most rules below name the test that
holds them. This library implements the shared header, tables and rollup; each
product owns the screens it draws on top of them.

## Shape of a report

    <blank>
      === Title ===                   header, bold + orange
      Tool › Title                    breadcrumb, dim
    <blank>
      ── Group ──                     only when there are two or more groups
      component | detail | result     column header, bold
      ----------+--------+---------   rule, dim
      <rows>
    <blank>
      N ok, M need attention.         rollup, always
    <blank>

Every line is indented two spaces. See the left-edge rule below.

## The rules

**Every surface closes with a rollup.** The reader learns one place to look for
"does this need me?". Five surfaces used to print a table and stop.
*Pinned by `agentbot/tests/test_report_contract.sh`.*

**A section rule appears only where a surface has two or more groups.** A rule
earns its place by separating things; above a single table it separates
nothing, and the header already names the surface. An unlabelled section draws
no rule and the table prints its own column header.
*Pinned by the same suite.*

**One table layout, one implementation.** The three-column and four-column
layouts live in `report_table.py` in the `dotfiles-shared` repository, which
both products resolve at runtime. The Bash renderer beside it in
`tui/report_table.sh` remains because first setup runs before a runtime exists,
and the two must agree byte for byte.
*Pinned by `agentbot/tests/test_renderer_parity.sh` across seven terminal
widths.*

**One palette and one vocabulary, in one place.** The eight escape codes and
both state vocabularies live in `dotfiles-shared` — `tui/colors.sh` for the
Bash renderer, `python/render_report.py` for the Python one, held to each other
by the parity suite. Agentbot held a third copy of all of it, which nothing
compared against either and which had drifted: it alone knew `unchanged`,
`ready`, `stale`, `none`, `reconcile`, `configure`, `install`, `apply` and
`merge`, and matched `latest ` and `refresh` as prefixes where the shared side
matched them whole. The union is now the one vocabulary, and Agentbot imports
it.
*Pinned by the renderer parity suite across all 53 states either vocabulary
knows.*

**Colour says state, and the two vocabularies are separate.**
`status_color_result` describes what something *is* — green for ok, red for
missing, yellow for check, dim for skipped, cyan for info. `status_color_action`
describes what will *happen* — so `current` is green there and `check` is
yellow. A skip is dim on purpose: it is a deliberate non-event and should
recede, not demand attention the way a warning does.
*Pinned by the same parity suite, across every state either mapping knows.*

**Width and colour are the caller's decision, never the renderer's.** The
layout module takes its width and home directory as arguments and returns a
string: no environment reading, no printing, no colour. Which terminal, and
whether colour is wanted, differ between the two tools and between interactive
and piped output.
*Pinned by `agentbot/tests/test_report_contract.sh`, which runs every surface
under a terminal as well as piped.*

**One left edge for everything a run prints.** Two spaces, matching the
header and the table. Headers, tables and prompts already sat there while step
lines, separator rules, the legend, the timing block and the closing lines sat
at column zero, so a single screen read as two documents laid on top of each
other. The indent covers prefixed lines, rules, the legend, `Install
finished`, `Install took` and its rows, `Done. Log saved to`, `Update
finished`, failure notices, every confirm prompt, the `dotfiles help` screen,
bootstrap's own `==>` markers, and git's output where the run prints it. Work
nested under a step — the progress animation, a quieted command's captured
output, the identity prompt's two fields — indents one level further.
*Established 2026-09-10 by sweeping every printf and echo in the product for a
format string that starts a visible line at the margin.*

**One heading style.** An orange `=== Title ===` over a dim breadcrumb, from
the shared header. The update run wrote its own yellow `== Title ==` with no
breadcrumb, and the repository gate a bold yellow label; both go through
`rt_print_header` now.

**A separator rule divides, so a section never opens on one.** The first step
in a section has nothing above it to be divided from.

**Progress lines survive a pipe.** `[STEP]` and `[OK]` carry no cursor control,
so they degrade cleanly. They were once emitted only to a terminal, which
switched them off for `dotfiles full-update` — the piped run where the silence
they exist to fix was longest.
*Pinned by `test_install_progress_survives_a_pipe`.*

**Every password prompt masks, with no exceptions.** One `*` per character,
backspace handled, the value never echoed. That covers both GitHub token
prompts and — since 2026-09-08 — sudo. It covered the SSH passphrase until
that component was removed on 2026-09-10.

The mask was briefly removed from sudo on 2026-09-10 in response to a report
that read as a request rather than the defect report it was, and restored the
same day. The rule stands as written: no exceptions.

sudo was the exception and is no longer one. Its own prompt echoes nothing at
all, and it is the prompt the operator sees most often. `SUDO_ASKPASS` replaces
it, which is the supported mechanism and the same one `ssh-askpass` uses: the
password goes from the terminal, through the helper, to sudo on stdout — never a
file, never a command line, never a log. sudo is primed once at the start of an
install, so its credential cache covers the other forty-seven call sites and the
operator is asked exactly once.

Where no terminal exists there is nothing to mask, and the helper steps aside
for sudo's own prompt rather than hanging on a closed handle.

**Non-zero means something is wrong, not something is pending.** A read-only
status command that finds a feature not yet installed has succeeded at its job.
`cursor status` used to exit 1 for that and disagreed with both its siblings and
its own diagnostics.

**A rollup over nothing is not an all-clear.** Zero ok, zero missing, zero
needing attention means nothing was inspected. `cli-config status` on a machine
with none of the three CLIs said "All 0 component(s) look good.", which reads as
a verdict on work that never happened. It reads `Nothing to report.` now, dim,
because it is a non-event.

**A section that expands on a problem appears only when there is one.** Check
Status carried a `Doctor issues` block on a healthy machine: a heading promising
issues, over a row invented to fill the table, under a Doctor row in the table
above that had already said "no issues". Three lines to repeat one.

**One vocabulary, and headings in sentence case.** `Update` is the
repository, configuration, skills and tools operation, in both products.
`Upgrade` is reserved for apt package upgrades, where it is the OS's own word.
Dotfiles used to print `Update report` and `Upgrade summary` in the same run and
open the work phase with `=== Upgrade ===`, so one operation had two names
before the two products were even compared. Headings are sentence case --
proper nouns and initialisms stay capitalised, nothing else does -- because
both products mixed `Skills install` with `Skills Prune`, and `Install summary`
with `Full Update`.
*Pinned by `tests/test_lexicon.sh` in both repositories, over the checker in
`dotfiles-shared/tests/lib/shared/lexicon.py`.*

**Ctrl-C is an answer, not a crash.** Force-quitting anywhere in either product
exits cleanly. Agentbot's menu used to print a Python traceback, which reads as
a defect the operator caused.

## The shape of a run

Added 2026-09-11, when Agentbot's surfaces were rebuilt on this shape. Dotfiles
already had it; the rules are written from what it does.

**Options, then a plan, then the run.** A command that changes a machine opens
with a component selector, then an execution plan saying what choosing that
means, then does it. The selector says *what* was chosen; the plan says what
choosing it *means*. Neither restates the other — an Agentbot install printed
`Selected: skills, graphify, boost` on a screen the operator had just left.

The plan asks once, in one shortcut format:

    c confirm   x confirm_forced   e edit   q back_to_menu :

`e` returns to the selector, so the two screens are a loop rather than a
one-way street. A product with nothing to force omits `x` rather than offering a
key that does what `c` does — Agentbot's install has no presence checks to
bypass, because skills reconcile from the lock every time and the integrations
set up idempotently.

**The repository gate runs before the selector, never after it.** A pull moves
the checkout and restarts the run, which throws a selection away. Both products
ask first.

**Every long-running command closes on where its time went.** How long it took,
then the slowest six components or steps. A run short enough to read whole lists
every row and drops the word "Slowest" — `dotfiles full-update` names all five
of its sections, because five of five called "the slowest" says nothing about
any of them.

A run that drives another product reports that product's own figure rather than
its own clock around it. Timing a phase from outside starts before its `sudo`
prompt, so the two numbers on one screen disagreed by however long the operator
spent typing a password.

**Tables take the width the terminal gives them.** Both renderers settle on the
same width by the same steps — `COLUMNS` first, then the terminal, then a floor.
Agentbot consulted the terminal only under `AGENTBOT_TUI` and sat at 80
otherwise, so a `full-update` printing tables from both came out two widths in
one session.

**Colour is applied where a line is printed, not by whoever is reading it.**
Agentbot's `[STEP]`/`[OK]` markers were painted by the Bash relay the menu runs
the backend under, so they were coloured under the menu and plain from a
terminal or inside a full update — the same markers, three appearances. The
relay still substitutes for anything plain and passes through a line that
already carries escapes, so nothing is wrapped twice.

## Navigation

Root menus offer an explicit `Quit`. Submenus accept `q` to go back and say so
in the hint. Both tools use the same hint strings, from the same shared runner.

A submenu that owns its own pause declares it; the parent then does not add a
second one.

## Resolved: the Bash renderer under a non-UTF-8 locale

Measured 2026-09-09. Under `LC_ALL=C` the Bash renderer laid out a row two
columns short: it slices and measures with `${var:0:n}` and `${#var}`, which
count bytes when the locale is not multibyte, so an em-dash cost three columns
to the Python renderer's one.

    LC_ALL=C.UTF-8  48 columns
    LC_ALL=C        46 columns

**Recorded as not worth fixing, and that judgement was wrong.** The estimate
was that the only fixes were a process-wide locale change made from a library,
"which reaches sorting and dates in whatever called it", or an ASCII fallback
the Python renderer would have to grow too.

Fixed 2026-09-10 with the first of those, at a cost the estimate had not
looked at closely enough:

- The change is conditional. A locale that already counts characters is left
  exactly as it is, so an operator's own `en_GB.UTF-8` is untouched. Only a
  locale that cannot count characters is replaced.
- The replacement is `C.UTF-8`, whose messages are English. The reach into
  "sorting and dates" is real but bounded, and it runs *towards* the
  assumption the probes already make: several of them grep English strings out
  of apt and git, which a non-English locale would have broken first.
- It costs no external process. `_rt_settle_locale` probes with `printf -v`
  and a subshell assignment to `LC_ALL`, which bash re-reads. An earlier draft
  used `locale -a | grep` and broke three Agentbot tests that run with a
  stripped PATH and assert an empty stderr — the pipeline's "grep: command not
  found" was not covered by the `2>/dev/null` on `locale`.

`tests/test_install_presentation.sh` pins the row width starting from
`LC_ALL=C`, so the two renderers cannot drift apart on such a machine again.

Left as written: the observation that every character involved is one a
C-locale terminal renders poorly anyway. That was true and is not why the
defect mattered — the parity between the two renderers was.

## What is deliberately not covered

The Dotfiles look is the reference and was not changed to match Agentbot;
Agentbot moved. Where this contract and the existing Dotfiles output disagreed,
the contract was written to describe Dotfiles.

Wording was the exception, added 2026-09-15. It had been out of scope, and the
gap showed: Dotfiles disagreed with itself inside a single run, so there was no
Dotfiles spelling to defer to. The lexicon rule above settles it for both.

Internal identifiers are still out of scope. Dotfiles keeps its `UPGRADE_STEP_*`
and `UPGRADE_RESULT_*` variables: renaming them changes no surface the operator
reads, and this contract governs what is printed.
