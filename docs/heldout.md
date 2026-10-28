# Unseen sheets: does it generalise?

Method vision-consensus, run 2026-10-28 by `uv run python eval/heldout.py`. No answer key: these show that ingest, detection and review flags run on other drafters' sheets, not how accurate they are. Overlays: docs/heldout/<file>.png (red ring = confidence under 90%, needs a look).

| File | Sheet | Plan/legend found by | Devices | Families | Needs a look | Suggested misses | Rooms | s | Tokens in | Error |
|---|---|---|---|---|---|---|---|---|---|---|
| elcentro-e1.pdf | E1 | model | 43 | 18 receptacle, 15 light, 7 switch, 2 detector, 1 fan | 1 | 0 | 6 | 56 | 211653 |  |
| gardengrove-a5-0.pdf | A5.0 | model | 34 | 14 receptacle, 10 light, 6 switch, 2 fan, 1 detector, 1 other | 2 | 0 | 6 | 40 | 208961 |  |
| lagunaniguel-a1-111.pdf | A1-111 | rules | 47 | 23 receptacle, 9 switch, 8 light, 4 fan, 2 detector, 1 other | 3 | 0 | 6 | 48 | 208372 |  |
| oc-p12.pdf | A2.1 | model | 60 | 25 receptacle, 15 light, 13 switch, 3 fan, 2 detector, 2 other | 13 | 0 | 8 | 57 | 209904 |  |
| santaana-a2-0.pdf | A2.0 | model | 88 | 32 light, 28 receptacle, 20 switch, 3 fan, 3 other, 2 detector | 5 | 0 | 10 | 74 | 208762 |  |
| sd-p5.pdf | A2 | model | 66 | 33 receptacle, 14 light, 9 switch, 5 other, 4 detector, 1 fan | 9 | 0 | 11 | 60 | 208566 |  |
| upland-ut1.pdf | UT-1 | model | 37 | 15 receptacle, 12 light, 5 switch, 3 other, 1 fan, 1 detector | 4 | 0 | 5 | 45 | 211649 |  |

Sources (public pre-approved ADU programs; most restrict reuse to their city, so the PDFs stay out of git): Escondido PAADU (the 4 key sheets), San Diego County PDS-670, Orange County (Design Path Studio), Laguna Niguel Plan 1 (public domain), Upland 440R, El Centro Plan B, Garden Grove ADUGO-CP-1, Santa Ana Craftsman.
