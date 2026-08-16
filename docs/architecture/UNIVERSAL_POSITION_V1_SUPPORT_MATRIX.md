# Universal Position v1 decoder support matrix

This matrix describes the identifier facts that Backgammon Engine Kit 0.3.0 can establish through its strict 15-checker XGID and complete GNUID profiles.

Legend:

- `decoded`: represented directly by the source and decoded by Engine Kit;
- `derived`: computed deterministically from decoded/profile-fixed facts;
- `requires_external_context`: not established by the source but may be supplied through validated enrichment;
- `unsupported`: represented by the source but intentionally outside the current decoder contract;
- `not_represented`: the source format does not encode the fact.

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
| Source view/orientation | derived from the fixed XGID top/bottom orientation | not_represented |

## How consumers should use the matrix

A fact marked `requires_external_context` is not permission to guess. Consumers may supply it only through the validated enrichment API, which can fill source-unknown fields but cannot overwrite decoded or derived facts.

A fact marked `unsupported` or `not_represented` remains explicit. Downstream adapters reject or return unavailable state when that fact is required for a safe operation.

The matrix is a compatibility statement for the 0.3.0 profiles, not a claim that every historical XGID or GNU Backgammon state can be losslessly represented across both formats.