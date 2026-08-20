# Licensing

Copyright © 2026 Marty Gale and contributors.

This repository contains software, authored research material, datasets, generated artifacts, and potentially third-party engine output. Different materials are licensed differently.

Before public release, include the complete legal texts in:

```text
LICENSES/AGPL-3.0-only.txt
LICENSES/CC-BY-SA-4.0.txt
LICENSES/CC-BY-4.0.txt
```

## 1. Software — GNU AGPL v3

Unless a file states otherwise, original software is licensed under the **GNU Affero General Public License, version 3 only**:

```text
SPDX-License-Identifier: AGPL-3.0-only
```

This includes:

- match runners;
- referee and state-progression code;
- dice generation and mirroring;
- seat-swap logic;
- engine orchestration;
- validation tools;
- post-analysis tools;
- release builders;
- dashboards and APIs;
- tests;
- experiment-execution code;
- build and maintenance scripts.

Official license text:

<https://www.gnu.org/licenses/agpl-3.0.html>

## 2. Authored research and explanatory material — CC BY-SA 4.0

Unless a file states otherwise, original authored research material is licensed under **Creative Commons Attribution-ShareAlike 4.0 International**:

```text
SPDX-License-Identifier: CC-BY-SA-4.0
```

This includes:

- methodology documents;
- research reports;
- written conclusions and interpretation;
- authored figures and explanatory diagrams;
- annotated disagreement positions;
- narrative release notes;
- educational notebooks where the primary work is prose and explanation.

Reusers must provide attribution, link to the license, indicate changes, and share adaptations under CC BY-SA 4.0 or a compatible license.

Official license:

<https://creativecommons.org/licenses/by-sa/4.0/>

### Recommended attribution

> Based on research from **Backgammon Engine Benchmarks** by Marty Gale and contributors.  
> Source: <https://github.com/backgammon-made-simple/backgammon-engine-benchmarks>  
> Licensed under **CC BY-SA 4.0**.  
> Changes were made.

## 3. Benchmark data and release artifacts — CC BY 4.0

Unless a file or release states otherwise, original benchmark datasets and release artifacts are licensed under **Creative Commons Attribution 4.0 International**, to the extent copyright or database rights apply:

```text
SPDX-License-Identifier: CC-BY-4.0
```

This may include:

- CSV, JSON, Parquet, or similar result tables;
- match-level and pair-level summaries;
- release manifests;
- validation-result datasets;
- derived numerical summaries;
- published metadata;
- project-authored logs and normalized output.

CC BY 4.0 permits sharing and adaptation, including commercial use, provided appropriate credit is given, the license is linked, and changes are indicated.

Official license:

<https://creativecommons.org/licenses/by/4.0/>

### Recommended dataset attribution

> **Backgammon Engine Benchmarks**, Marty Gale and contributors.  
> Dataset or release: [release name and version].  
> Source: <https://github.com/backgammon-made-simple/backgammon-engine-benchmarks>  
> Licensed under **CC BY 4.0**.

Use the release-specific citation when one is provided.

## 4. Generated facts and third-party output

Not every fact, number, engine output, or automatically generated record is protected by copyright in every jurisdiction.

The CC licenses apply only to rights the project is legally able to license.

Output produced by Sage, GNU Backgammon, or another engine may also be subject to that engine's license, terms, or other legal considerations. This repository does not relicense third-party engines, binaries, neural-network weights, or assets.

Third-party material should be documented in `THIRD_PARTY_NOTICES.md` and in release metadata when relevant.

## 5. Mixed notebooks and reports

A notebook may contain software, prose, figures, and data.

Use explicit notices:

- executable software cells: AGPL-3.0-only;
- authored explanatory prose and figures: CC BY-SA 4.0;
- exported benchmark datasets: CC BY 4.0 where applicable.

When practical, separate these materials into distinct files.

## 6. Attribution and official status

The licenses require attribution as described in their terms.

No license grants permission to imply that a fork, study, service, or modified release is official, affiliated with, sponsored by, or endorsed by Backgammon Made Simple or the original authors.

Forks and replications are welcome. They should preserve provenance and clearly identify modifications to protocols, code, data, or conclusions.

## 7. Contributions

Unless explicitly agreed otherwise in writing:

- software contributions are AGPL-3.0-only;
- report, methodology, and authored-figure contributions are CC BY-SA 4.0;
- contributed datasets intended for public release are CC BY 4.0, provided the contributor has authority to license them.

## 8. No warranty

Software, research material, and datasets are provided without warranty under their applicable license terms.

This summary is for clarity and does not replace the full legal texts.
