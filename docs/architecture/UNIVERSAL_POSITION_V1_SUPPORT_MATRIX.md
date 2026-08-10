# Universal Position v1 decoder support matrix

Legend: `decoded`, `derived`, `requires_external_context`, `unsupported`,
`not_represented`.

| Semantic field or state | XGID 15-checker profile | GNU combined-ID 15-checker profile |
|---|---|---|
| Stable players | derived from top/bottom mapping | derived from GNU player number |
| Points | decoded | decoded |
| Bar | decoded | decoded |
| Checker count | derived from named profile | derived from named profile |
| Off | derived | derived |
| On-roll | decoded | decoded from DiceOwner |
| Decision player | derived | decoded from TurnOwner, then validated |
| Dice | decoded | decoded |
| Cube enabled | requires_external_context | requires_external_context |
| Accepted cube value | decoded | decoded |
| Accepted cube owner | decoded | decoded |
| Maximum cube | decoded | requires_external_context |
| Pending ordinary double | decoded | decoded |
| Pending beaver | decoded | not_represented |
| Pending raccoon | decoded | not_represented |
| Pending resignation | unsupported | decoded |
| Score | decoded | decoded |
| Match length | decoded | decoded |
| Crawford | decoded or derived for money play | decoded |
| Jacoby | decoded for money play | requires_external_context |
| Beavers | decoded for money play | requires_external_context |
| Raccoons | not_represented | not_represented |
| Automatic doubles | not_represented | not_represented |
| Variation | not_represented | not_represented |
| Game state | derived as active post-opening state | decoded |
| Arbitrary source view | decoded | not_represented |

Unsupported or unresolved fields are rejected when a downstream adapter requires
them. The matrix describes this bounded milestone, not a claim of universal
support for every historical source variant.
