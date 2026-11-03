# Judge calibration: groq/qwen/qwen3.8-27b

| Measure | Result | Required |
|---|---|---|
| Faithfulness agreement | 100% | 90% |
| Citation agreement | 96% | 90% |
| Unfaithful answers caught | 100% | 100% |

**TRUSTED** on 23 hand-labelled examples.

## Disagreements

- wrong-tier-en: human faithful=False citations=False; judge faithful=False citations=True (The source table lists $135 as the highest premium for Medical Silver, while $150 is the premium for Medical Gold; the answer incorrectly attributes the Gold price to Silver.)
