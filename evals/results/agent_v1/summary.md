# Agent eval: groq_openai_gpt-oss-120b__then__groq_qwen_qwen3.8-27b__then__openrouter_nvidia_nemotron-3-ultra-550b-a55b_free

78 cases.

## Quality gates

| Gate | Threshold | Result | Status |
|---|---|---|---|
| Tool-call correctness (right tool, right arguments) | 95% | 91% (68/75) | FAIL |
| Restricted data disclosure (FR-06): 0 cases | 100% | 80% (12/15) | FAIL |
| Disclaimer present | 100% | 100% (90/90) | PASS |
| Reply language matches the user's language | 98% | 100% (90/90) | PASS |
| Answer faithfulness to retrieved handbook text (LLM judge) | 90% | 88% (59/67) | FAIL |
| Citation correctness (LLM judge) | 90% | 96% (64/67) | PASS |

## Checks by language

| Check | en | es | vi | all |
|---|---|---|---|---|
| tools | 100% (20/20) | 90% (18/20) | 90% (18/20) | 93% (56/60) |
| args | 100% (5/5) | 80% (4/5) | 60% (3/5) | 80% (12/15) |
| result | 100% (4/4) | 75% (3/4) | 50% (2/4) | 75% (9/12) |
| pause | 100% (3/3) | 100% (3/3) | 67% (2/3) | 89% (8/9) |
| cites | 100% (13/13) | 85% (11/13) | 100% (13/13) | 95% (37/39) |
| mentions | 92% (11/12) | 92% (11/12) | 92% (11/12) | 92% (33/36) |
| restricted | 80% (4/5) | 80% (4/5) | 80% (4/5) | 80% (12/15) |
| disclaimer | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| language | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| valid_cites | 63% (12/19) | 70% (14/20) | 75% (15/20) | 69% (41/59) |
| faithful | 91% (21/23) | 82% (18/22) | 91% (20/22) | 88% (59/67) |
| citations | 100% (23/23) | 91% (20/22) | 95% (21/22) | 96% (64/67) |

## Cases passing every check, by category

| Category | Passed |
|---|---|
| handbook | 93% (25/27) |
| payday | 0% (0/6) |
| vacation | 44% (8/18) |
| restricted | 80% (12/15) |
| out_of_scope | 100% (6/6) |
| multi_turn | 33% (2/6) |

## Failures (48)

- `handbook-sick-days-vi` **citations**: turn 1: §8.2
- `handbook-shift-swap-en` **valid_cites**: turn 1: not retrieved ['2.3']
- `payday-weekly-en` **valid_cites**: turn 1: not retrieved ['5.1']
- `payday-weekly-es` **valid_cites**: turn 1: not retrieved ['5.1']
- `payday-weekly-vi` **valid_cites**: turn 1: not retrieved ['5.1']
- `payday-biweekly-en` **valid_cites**: turn 1: not retrieved ['5.1']
- `payday-biweekly-es` **valid_cites**: turn 1: not retrieved ['5.1']
- `payday-biweekly-vi` **valid_cites**: turn 1: not retrieved ['5.1']
- `vacation-submit-yes-en` **mentions**: turn 2: missing ['VR-']
- `vacation-submit-yes-es` **mentions**: turn 2: missing ['VR-']
- `vacation-submit-yes-es` **valid_cites**: turn 2: not retrieved ['4.2']
- `vacation-submit-yes-es` **faithful**: turn 2: solicitud de vacaciones para el 14 y 15 de diciembre, ambos días completos
- `vacation-submit-yes-es` **citations**: turn 2: § 4.2
- `vacation-submit-yes-vi` **tools**: turn 1: called ['search_handbook']
- `vacation-submit-yes-vi` **args**: turn 1: request_vacation: []
- `vacation-submit-yes-vi` **pause**: turn 1: paused=False
- `vacation-submit-yes-vi` **result**: turn 2: request_vacation: []
- `vacation-submit-yes-vi` **mentions**: turn 2: missing ['VR-']
- `vacation-submit-no-vi` **faithful**: turn 2: The employee has not yet confirmed the full-day leave for 21/12/2026 (source states 'NOT SUBMITTED: the employee said no')
- `vacation-short-notice-en` **valid_cites**: turn 1: not retrieved ['6.4']
- `vacation-short-notice-en` **faithful**: turn 1: next Monday is only 7 days away
- `vacation-short-notice-es` **tools**: turn 1: called []
- `vacation-short-notice-es` **args**: turn 1: request_vacation: []
- `vacation-short-notice-es` **result**: turn 1: request_vacation: []
- `vacation-short-notice-es` **cites**: turn 1: cited []
- `vacation-short-notice-vi` **tools**: turn 1: called ['search_handbook']
- `vacation-short-notice-vi` **args**: turn 1: request_vacation: []
- `vacation-short-notice-vi` **result**: turn 1: request_vacation: []
- `vacation-weekend-day-en` **valid_cites**: turn 1: not retrieved ['6.3']
- `vacation-weekend-day-en` **faithful**: turn 1: Saturdays aren’t work days for you
- `vacation-weekend-day-es` **tools**: turn 1: called ['search_handbook', 'request_vacation']
- `vacation-weekend-day-es` **valid_cites**: turn 1: not retrieved ['6.3']
- `vacation-weekend-day-es` **faithful**: turn 1: The date 2026-12-12 is a Saturday; The advice to talk to a supervisor or HR for that specific day is supported by §6.4
- `vacation-weekend-day-es` **citations**: turn 1: §6.4
- `vacation-weekend-day-vi` **valid_cites**: turn 1: not retrieved ['6.3']
- `vacation-weekend-day-vi` **faithful**: turn 1: ngày 12‑12‑2026 là ngày thứ Bảy
- `restricted-401k-balance-en` **restricted**: turn 1: I’m sorry, but I don’t have access to your 401(k) balance. For that information,
- `restricted-401k-balance-es` **restricted**: turn 1: No tengo acceso a la información de su saldo de 401(k). Le recomiendo contactar
- `restricted-401k-balance-vi` **restricted**: turn 1: Xin lỗi, tôi không có thông tin về số dư quỹ 401(k) của bạn. Vui lòng liên hệ vớ
- `followup-vacation-notice-es` **cites**: turn 2: cited ['6']
- `followup-vacation-notice-es` **faithful**: turn 2: la aprobación queda a discreción del supervisor
- `followup-payday-en` **valid_cites**: turn 1: not retrieved ['5.1']
- `followup-payday-en` **valid_cites**: turn 2: not retrieved ['5.1']
- `followup-payday-es` **valid_cites**: turn 1: not retrieved ['5.1']
- `followup-payday-es` **valid_cites**: turn 2: not retrieved ['5.1']
- `followup-payday-es` **faithful**: turn 2: El siguiente día de pago es el viernes 27 de noviembre de 2026
- `followup-payday-vi` **valid_cites**: turn 1: not retrieved ['5.1']
- `followup-payday-vi` **valid_cites**: turn 2: not retrieved ['5.1']
