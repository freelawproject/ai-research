<!-- FILED 2026-09-08 as https://github.com/freelawproject/eyecite/issues/349 -->

# `id.` after a record citation (`Compl.`) resolves to the previous case citation

## Summary

`id.` is resolved to the most recent resolved case citation, even when the text it actually refers back to is a record or pleading citation that eyecite does not extract (`Compl.`, `Answer`, `Dkt. No.`, `Ex. A`, `Tr.`). Because those citations are invisible to the resolver, every `id.` that follows them is chained to whatever case citation came last in the document, and the pin cite (`Claim V ¶¶ 58-71`) is not parsed, so the pin-cite sanity check never rejects the match.

## Example

From *Sidhu v. Bayer Healthcare Pharmaceuticals Inc.* (N.D. Cal. Nov. 22, 2022), https://www.courtlistener.com/opinion/9606979/case/. The sentence before this passage ends with a short cite to *Vess*, 317 F.3d at 1105:

> Plaintiff brings several common law and consumer protection claims that sound in fraud: (1) common law fraud, Compl. Claim III ¶¶ 46-53; (2) violation of the fraud prong of the UCL, id. Claim V ¶¶ 58-71; (3) violation of the CLRA, id. Claim VI ¶¶ 72-92; and (4) violation of the FAL, id. Claim VII ¶¶ 93-99.

All three `id.`s refer to the complaint (`Compl.`). eyecite 2.7.8 resolves all three to *Vess*, 317 F.3d 1097, so the opinion appears to cite *Vess* four more times than it does.

Ideally the resolver would recognize common record-citation abbreviations as antecedents, so an `id.` that follows one is left unresolved rather than attached to the last case. A cheaper heuristic: an `id.` whose "pin cite" is not a page (`Claim V ¶¶ 58-71`, `¶ 12`, `at 3:14`, `Ex. B`) is unlikely to point at a reporter citation.
