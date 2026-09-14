<!-- FILED 2026-09-08 as https://github.com/freelawproject/eyecite/issues/348 -->

# `Id.` resolves to the citation inside a parenthetical

## Summary

`resolve_citations` resolves an `Id.` to the resource of the most recently *resolved* citation, whatever that citation was. When the preceding citation sits inside an explanatory parenthetical (`(citing X)`, `(quoting Y)`), the `Id.` attaches to X or Y rather than to the citation the parenthetical belongs to. In the same construction, if the parenthetical's citation is a short form with no full cite in the text, the `Id.` is dropped entirely because "the last resolution failed".

## Example

From *Sidhu v. Bayer Healthcare Pharmaceuticals Inc.* (N.D. Cal. Nov. 22, 2022), https://www.courtlistener.com/opinion/9606979/case/ (line-number artifacts removed):

> 379 F. Supp. 3d 809, 821 (N.D. Cal. 2019) (citing Levine, 555 U.S. at 571). The court in Holley discussed preemption as to failure to warn claims and design defect claims. In order to plead a failure to warn claim based on information that came out after the initial drug release, a plaintiff would need to plead "a labeling deficiency that [Defendants] could have corrected using the CBE regulation." Id. at 827 (quoting Gibbons v. Bristol-Myers Squibb Co., 919 F.3d 699, 708 (2d Cir. 2019)). The Holley court chose not to apply preemption to design defect claims based on drug composition. Id. at 821-25.

Both `Id.`s refer to *Holley*, 379 F. Supp. 3d 809 — the pin cites 827 and 821-25 are pages of that reporter volume, and the intervening citations are inside `(citing …)` / `(quoting …)` parentheticals that modify the *Holley* cite and the `Id.` respectively.
