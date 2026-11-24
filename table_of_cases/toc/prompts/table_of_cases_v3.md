You are transcribing one scanned page from the Table of Cases of a West Decennial Digest into structured records.

## The page

- The page has three columns. Read the left column top to bottom, then the middle column, then the right column.
- Each entry starts with a case name in bold type and has a hanging indent: the first line starts at the left edge of the column and the following lines are indented.
- An entry has these parts, in this order:
  1. Case name, in bold. It may be inverted, for example `AAA Expedited Freight Systems, Inc.; Hackett v.` or `A.A., In re`.
  2. Court abbreviation, for example `NYAD 2 Dept`, `CA5 (Tex)`, `GaApp`, `SDNY`.
  3. One or more citations to the decision itself, separated by commas, for example `865 NYS2d 101, 54 AD3d 721`.
  4. Sometimes later history: a phrase such as `aff`, `rev`, `cert den`, `reh den`, `review den`, `leave to appeal den`, `on remand`, usually followed by more citations.
  5. A dash (`.—`), then digest topics and key numbers, for example `Judgm 185.3(21); Plead 325.`
- An entry can run from the bottom of one column to the top of the next. Treat it as one entry.
- The large heading at the top of the page (for example `AAMODT;`), the volume and page marker (for example `51 12th D Pt 1—2`), the footer line about KeyCite, section letters, and titles such as `TABLE OF CASES` are not entries.

## What to return

Return one record per entry, in reading order, with these fields:

- `name`: the bold case name exactly as printed, keeping the inverted form and punctuation. Do not include the comma that separates it from the court.
- `court`: the court abbreviation exactly as printed, or null if none is printed.
- `citations`: the citations to the decision itself, one string per citation, in printed order. Include parenthetical citations and docket-style citations printed among them, for example `2007-0625 (LaApp 1 Cir 9/19/07)` or `2008 WL 5210931`. Stop at the first history phrase.
- `history`: the later history, as a list of items in printed order. An empty list if there is none. One item is one run of consecutive history phrases together with the citations that follow that run. A new item starts only after a citation. Each item has:
  - `action`: all the consecutive history phrases up to the first case name or citation, exactly as printed, as ONE string, however many actions it names. Keep the commas and the word `and` between them. Never split a run of phrases into several items. Examples of a single `action`: `aff`; `cert den`; `reh and reh den`; `aff in part, vac in part, rev in part`; `reh overr, and review den, and reh of petition for review den`.
  - `case_name`: the case name printed between the action and its citations, if any, exactly as printed. Null when no case name is printed, which is the usual case.
  - `citations`: the citations that follow the action, one string per citation, in printed order. An empty list only when the history ends without a citation.
  Examples:
  - `reh den, cert den Anderson v. Southland Bank, 130 SCt 275, 175 LEd2d 184` is ONE item: action `reh den, cert den`, case name `Anderson v. Southland Bank`, citations `130 SCt 275` and `175 LEd2d 184`.
  - `leave to appeal gr 913 NYS2d 128, 15 NY3d 714, rev 927 NYS2d 618` is TWO items: action `leave to appeal gr` with citations `913 NYS2d 128` and `15 NY3d 714`, then action `rev` with citation `927 NYS2d 618`.
  - `aff in part, vac in part, rev in part 647 F3d 202` is ONE item: action `aff in part, vac in part, rev in part` with citation `647 F3d 202`.
  - `reh den, and cert den` is ONE item: action `reh den, and cert den` with no citations.
- `continued_from_previous_page`: true only for text at the very top of the left column that comes before the first bold case name. That text is the end of an entry from the previous page. Put any citations or history in it into the fields they belong to and set `name` to an empty string. If it holds only digest topics, return it with empty fields.
- `continues_on_next_page`: true only for the last entry of the right column when it is cut off by the end of the page.

Also return `running_head`: the large heading printed at the top of the page, exactly as printed, or null.

Do not transcribe the digest topics and key numbers. Everything after the dash (`.—`) in an entry is left out; the record ends at the dash.

## Rules

- Transcribe only what is printed. Do not correct, expand, normalize, or complete anything. Do not add entries, citations, or numbers that are not on the page.
- Copy every digit and every parenthetical exactly. `130 SCt 275` and `130 SCt 278` are different.
- Keep the printed spelling of reporter abbreviations, for example `NYS2d`, `FSupp2d`, `SE2d`, `FedAppx`.
- When a word is split by a hyphen at the end of a line, join it back together (`Fe-` + `dAppx` becomes `FedAppx`). Keep hyphens that belong to the word.
- If a character is unreadable, write your best reading. Do not skip the entry.
- Do not skip any entry. Every bold case name on the page must appear once.
