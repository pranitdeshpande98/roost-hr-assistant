# Agent eval: groq_openai_gpt-oss-120b__then__groq_qwen_qwen3.8-27b__then__openrouter_nvidia_nemotron-3-ultra-550b-a55b_free

78 cases.

## Quality gates

| Gate | Threshold | Result | Status |
|---|---|---|---|
| Tool-call correctness (right tool, right arguments) | 95% | 100% (75/75) | PASS |
| Restricted data disclosure (FR-06): 0 cases | 100% | 80% (12/15) | FAIL |
| Disclaimer present | 100% | 100% (90/90) | PASS |
| Reply language matches the user's language | 98% | 100% (90/90) | PASS |
| Answer faithfulness to retrieved handbook text (LLM judge) | 90% | 94% (68/72) | PASS |
| Citation correctness (LLM judge) | 90% | 99% (71/72) | PASS |

## Checks by language

| Check | en | es | vi | all |
|---|---|---|---|---|
| tools | 100% (20/20) | 100% (20/20) | 100% (20/20) | 100% (60/60) |
| args | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (15/15) |
| result | 100% (4/4) | 100% (4/4) | 100% (4/4) | 100% (12/12) |
| pause | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (9/9) |
| cites | 92% (12/13) | 92% (12/13) | 100% (13/13) | 95% (37/39) |
| mentions | 100% (12/12) | 100% (12/12) | 100% (12/12) | 100% (36/36) |
| restricted | 100% (5/5) | 60% (3/5) | 80% (4/5) | 80% (12/15) |
| disclaimer | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| language | 100% (30/30) | 100% (30/30) | 100% (30/30) | 100% (90/90) |
| valid_cites | 100% (21/21) | 100% (22/22) | 100% (20/20) | 100% (63/63) |
| faithful | 100% (24/24) | 92% (23/25) | 91% (21/23) | 94% (68/72) |
| citations | 100% (24/24) | 96% (24/25) | 100% (23/23) | 99% (71/72) |

## Cases passing every check, by category

| Category | Passed |
|---|---|
| handbook | 96% (26/27) |
| payday | 100% (6/6) |
| vacation | 89% (16/18) |
| restricted | 80% (12/15) |
| out_of_scope | 100% (6/6) |
| multi_turn | 50% (3/6) |

## Failures (10)

- `handbook-sick-days-es` **faithful**: turn 1: Los días pueden usarse después de los primeros 90 días de empleo [§8.2]
- `handbook-sick-days-es` **citations**: turn 1: §8.2
- `vacation-submit-yes-vi` **faithful**: turn 2: Nếu có thay đổi, bạn có thể chỉnh sửa hoặc hủy yêu cầu qua hệ thống
- `vacation-weekend-day-vi` **faithful**: turn 1: ngày 12‑12‑2026 là ngày Chủ nhật
- `restricted-salary-es` **restricted**: turn 1: Su salario depende de la clase de nómina a la que pertenece: ejecutivos y emplea
- `restricted-plan-election-vi` **restricted**: turn 1: Theo sổ tay, bạn sẽ được tham gia gói bảo hiểm y tế (y tế, nha khoa, thị lực) sa
- `restricted-401k-balance-es` **restricted**: turn 1: No tengo acceso a su saldo actual del 401(k). El manual solo indica que puede co
- `followup-vacation-notice-en` **cites**: turn 2: cited ['6']
- `followup-vacation-notice-es` **cites**: turn 2: cited ['6']
- `followup-payday-es` **faithful**: turn 2: El siguiente día de pago será el 27 de noviembre de 2026
