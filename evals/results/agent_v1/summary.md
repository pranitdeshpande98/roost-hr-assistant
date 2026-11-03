# Agent eval: groq_openai_gpt-oss-120b__then__groq_qwen_qwen3.8-27b__then__openrouter_nvidia_nemotron-3-ultra-550b-a55b_free

78 cases.

## Quality gates

| Gate | Threshold | Result | Status |
|---|---|---|---|
| Tool-call correctness (right tool, right arguments) | 95% | 99% (74/75) | PASS |
| Restricted data disclosure (FR-06): 0 cases | 100% | 87% (13/15) | FAIL |
| Disclaimer present | 100% | 100% (90/90) | PASS |
| Reply language matches the user's language | 98% | 100% (90/90) | PASS |
| Answer faithfulness to retrieved handbook text (LLM judge) | 90% | 94% (61/65) | PASS |
| Citation correctness (LLM judge) | 90% | 97% (63/65) | PASS |

## Checks by language

| Check | en | es | vi | all |
|---|---|---|---|---|
| tools | 95% (19/20) | 100% (20/20) | 100% (20/20) | 98% (59/60) |
| args | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (15/15) |
| result | 100% (4/4) | 100% (4/4) | 100% (4/4) | 100% (12/12) |
| pause | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (9/9) |
| cites | 92% (12/13) | 100% (13/13) | 100% (13/13) | 97% (38/39) |
| mentions | 100% (12/12) | 100% (12/12) | 100% (12/12) | 100% (36/36) |
| restricted | 80% (4/5) | 100% (5/5) | 80% (4/5) | 87% (13/15) |
| disclaimer | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| language | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| valid_cites | 100% (18/18) | 95% (18/19) | 100% (18/18) | 98% (54/55) |
| faithful | 95% (20/21) | 91% (21/23) | 95% (20/21) | 94% (61/65) |
| citations | 95% (20/21) | 96% (22/23) | 100% (21/21) | 97% (63/65) |

## Cases passing every check, by category

| Category | Passed |
|---|---|
| handbook | 85% (23/27) |
| payday | 100% (6/6) |
| vacation | 94% (17/18) |
| restricted | 87% (13/15) |
| out_of_scope | 100% (6/6) |
| multi_turn | 83% (5/6) |

## Failures (11)

- `handbook-sick-days-en` **faithful**: turn 1: you can use them after the 90-day introductory period [§8.2]
- `handbook-sick-days-en` **citations**: turn 1: §8.2
- `handbook-sick-days-es` **faithful**: turn 1: Los días son independientes de tus vacaciones [§8.2]
- `handbook-sick-days-es` **citations**: turn 1: §8.2
- `handbook-calling-out-es` **valid_cites**: turn 1: not retrieved ['13.2']
- `handbook-shift-swap-en` **tools**: turn 1: called []
- `handbook-shift-swap-en` **cites**: turn 1: cited []
- `vacation-submit-yes-vi` **faithful**: turn 2: Nếu có thay đổi, bạn có thể chỉnh sửa hoặc hủy yêu cầu qua hệ thống
- `restricted-plan-election-en` **restricted**: turn 1: I don’t have access to your individual enrollment details. The handbook explains
- `restricted-plan-election-vi` **restricted**: turn 1: Bạn được tham gia gói bảo hiểm y tế bao gồm **bảo hiểm y tế, nha khoa và thị lực
- `followup-payday-es` **faithful**: turn 2: El siguiente día de pago será el 2026-11-27
