# Evidence-gated adapter fixtures

Verified GNU 1.08.003 and BGSage 1.2.20260706 checker/cube evidence is retained
under `evidence/gnu/1.08.003/` and `evidence/sage/1.2.20260706/`. This directory
tracks evidence still missing for other GNU and Sage settings, including Sage
rollout. Contract-only model tests continue to leave unavailable measurements
as JSON `null`.

A future engine fixture is accepted only when its immutable raw output is
paired with:

- the complete input position and decision context;
- engine name and verified version;
- requested analysis setting and actual setting when reported;
- configuration identity and model/weights identity when known;
- invocation identity and parser version;
- capture timestamp and content digest;
- a documented reproduction or provenance record.

Generated placeholder cache entries are not evidence.
