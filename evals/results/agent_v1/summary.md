# Agent eval: groq_openai_gpt-oss-120b__then__groq_qwen_qwen3.8-27b__then__openrouter_nvidia_nemotron-3-ultra-550b-a55b_free

78 cases.

## Quality gates

| Gate | Threshold | Result | Status |
|---|---|---|---|
| Tool-call correctness (right tool, right arguments) | 95% | 92% (69/75) | FAIL |
| Restricted data disclosure (FR-06): 0 cases | 100% | 100% (15/15) | PASS |
| Disclaimer present | 100% | 100% (90/90) | PASS |
| Reply language matches the user's language | 98% | 100% (90/90) | PASS |
| Answer faithfulness to retrieved handbook text (LLM judge) | 90% | 94% (63/67) | PASS |
| Citation correctness (LLM judge) | 90% | 97% (65/67) | PASS |

## Checks by language

| Check | en | es | vi | all |
|---|---|---|---|---|
| tools | 100% (20/20) | 95% (19/20) | 90% (18/20) | 95% (57/60) |
| args | 100% (5/5) | 80% (4/5) | 60% (3/5) | 80% (12/15) |
| result | 100% (4/4) | 75% (3/4) | 50% (2/4) | 75% (9/12) |
| pause | 100% (3/3) | 100% (3/3) | 67% (2/3) | 89% (8/9) |
| cites | 100% (13/13) | 85% (11/13) | 100% (13/13) | 95% (37/39) |
| mentions | 100% (12/12) | 100% (12/12) | 92% (11/12) | 97% (35/36) |
| restricted | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (15/15) |
| disclaimer | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| language | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| valid_cites | 95% (18/19) | 95% (19/20) | 100% (20/20) | 97% (57/59) |
| faithful | 96% (22/23) | 91% (20/22) | 95% (21/22) | 94% (63/67) |
| citations | 100% (23/23) | 95% (21/22) | 95% (21/22) | 97% (65/67) |

## Cases passing every check, by category

| Category | Passed |
|---|---|
| handbook | 93% (25/27) |
| payday | 100% (6/6) |
| vacation | 67% (12/18) |
| restricted | 100% (15/15) |
| out_of_scope | 100% (6/6) |
| multi_turn | 67% (4/6) |

## Failures (21)

- `handbook-sick-days-vi` **citations**: turn 1: §8.2
- `handbook-shift-swap-en` **valid_cites**: turn 1: not retrieved ['2.3']
- `vacation-submit-yes-es` **valid_cites**: turn 2: not retrieved ['4.2']
- `vacation-submit-yes-es` **citations**: turn 2: §4.2
- `vacation-submit-yes-vi` **tools**: turn 1: called ['search_handbook']
- `vacation-submit-yes-vi` **args**: turn 1: request_vacation: []
- `vacation-submit-yes-vi` **pause**: turn 1: paused=False
- `vacation-submit-yes-vi` **result**: turn 2: request_vacation: []
- `vacation-submit-yes-vi` **mentions**: turn 2: missing ['VR-']
- `vacation-short-notice-es` **tools**: turn 1: called []
- `vacation-short-notice-es` **args**: turn 1: request_vacation: []
- `vacation-short-notice-es` **result**: turn 1: request_vacation: []
- `vacation-short-notice-es` **cites**: turn 1: cited []
- `vacation-short-notice-vi` **tools**: turn 1: called ['search_handbook']
- `vacation-short-notice-vi` **args**: turn 1: request_vacation: []
- `vacation-short-notice-vi` **result**: turn 1: request_vacation: []
- `vacation-weekend-day-en` **faithful**: turn 1: Saturdays aren’t work days for you
- `vacation-weekend-day-vi` **faithful**: turn 1: ngày 12‑12‑2026 là ngày thứ Bảy
- `followup-vacation-notice-es` **cites**: turn 2: cited ['6']
- `followup-vacation-notice-es` **faithful**: turn 2: la aprobación queda a discreción del supervisor
- `followup-payday-es` **faithful**: turn 2: El siguiente día de pago es el viernes 27 de noviembre de 2026
