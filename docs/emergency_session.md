# Golden Demo Emergency Session Contract

## 1. 범위

Phase 18-A는 해결된 Golden Demo Run을 T+90에서 T+240으로 진행하고, 기존 Synthetic
`EVT-MIL-T01-EMERGENCY`를 Session·Exception Queue·Web UI에 동일한 Checkpoint로 노출한다.
Phase 18-B는 이 비상 증거에서 조정 후보를 생성하고 Phase 18-C는 각 후보를 원 Traffic의 복사본에서
격리 검증한다. Phase 18-D는 검증된 안전 후보만 결정론적으로 정렬하고, Phase 18-E는 관제사의
Accept/Modify/Reject를 별도 Audit으로 기록한다. Phase 18-F는 승인된 계획의 명시적 적용과 T+260
비상 회복 증거를 연결한다.

## 2. 결정론적 전환

`ADVANCE_TO_EMERGENCY`는 `CONFLICT_RESOLVED`, T+90에서만 허용된다. Command Service는 고정된
150초를 진행하고 새 Step을 실행한다. 결과는 `EMERGENCY_DECLARED`, T+240이며 같은 명령의 중복이나
다른 Stage의 호출은 상태를 바꾸지 않고 거부된다.

Session은 적용 결과와 최신 Step 중 더 늦은 시각의 Traffic을 표시한다. 두 결과가 같은 T+90이면
승인 기동이 반영된 적용 결과를 유지하고, T+240 Step이 생기면 최신 Traffic으로 전환한다.

## 3. Emergency 증거

Session의 `emergency`는 다음 증거를 하나의 JSON-ready 객체로 결합한다.

- Event ID, 선언 UTC, 대상 항공기, 비상 유형과 비식별 사유 분류
- 독립된 Operational Priority Assessment ID, Level, Score와 Reason Code
- Source Event ID, Exception Queue ID와 1부터 시작하는 현재 순위

Golden Scenario에서는 `MIL-T01`, `PRIORITY_RETURN`, `AIRCRAFT_CONDITION`, `EMERGENCY / 100`,
Queue Rank 1이 고정된다. Conflict Risk와 Operational Priority는 서로 다른 결과이며 UI도 이를 명시한다.

## 4. Human-in-the-loop 경계

비상 선언은 관제사가 검토할 예외를 생성하지만 Aircraft Runtime의 `emergency_status`나 기동을 직접
바꾸지 않는다. 따라서 T+240에서도 Runtime은 승인 전 상태를 유지한다. 후속 단계에서 비상 복귀 후보,
격리 검증, Accept/Modify/Reject와 승인 적용을 별도 계약으로 연결한다.

## 5. 화면 동기화

Playback이 T+240 Cue에 도달하면 정확한 Frame에서 자동 정지한 뒤
`ADVANCE_TO_EMERGENCY`를 호출한다. Session 응답이 도착하면 다음을 함께 표시한다.

- `EMERGENCY_DECLARED` Stage와 Emergency 증거 패널
- `MIL-T01` Radar Marker/Trail 강조
- `EXCEPTION-PRIORITY-MIL-T01` Queue 최상위, `EMERGENCY / 100`
- 기존 원 충돌 및 Post-action Revalidation 증거 보존

## 6. Phase 18-B 비상 복귀 후보

기존 Conflict Candidate는 한 Conflict Pair의 단일 기동을 표현한다. 비상 복귀는 대상 항공기의 순서
상승과 주변 Traffic 조정을 한 계획으로 묶어야 하므로 `EmergencyReturnCandidateBatch`를 별도로 둔다.
각 후보는 Arrival Sequence, 0개 이상의 Aircraft Action, 안정 접근기 보존 여부와 단순 Cost를 가진다.

| 후보 | 전략 | Arrival Sequence / Action | 생성 단계 상태 |
|---|---|---|---|
| `ER-CAND-A` | `PROTECTED_PRIORITY_RETURN` | `CIV-A01` 뒤 `MIL-T01`, `CIV-A02` 30 kt 감속, `MIL-F02` 30초 지연 | `NOT_VALIDATED` |
| `ER-CAND-B` | `PRIORITY_SEQUENCE_ONLY` | `MIL-T01`을 2번으로 이동, 주변 Traffic 조정 없음 | `NOT_VALIDATED` |
| `ER-CAND-C` | `IMMEDIATE_LEAD` | `MIL-T01`을 1번으로 이동해 안정된 `CIV-A01`을 뒤로 이동 | `NOT_VALIDATED` |
| `ER-CAND-D` | `NO_ACTION` | 기존 순서를 유지하는 비교 기준선 | `NOT_VALIDATED` |

후보 ID와 출력 순서는 입력 Iterable 순서와 무관하다. `ER-CAND-A`의 추천 여부를 생성 단계에서 미리
결정하지 않으며, `preserves_stabilized_arrival`도 Safety 판정이 아닌 후속 검증 입력 증거다. 속도 목표는
대상 Aircraft Performance Profile의 최소속도보다 낮아지지 않는다.

Session의 `emergency_return_candidates`는 Source Exception/Priority Assessment ID, 생성 UTC,
Generator Profile과 네 후보를 JSON Primitive로 제공한다. 후보 생성 조회는 Clock, Queue, Aircraft
Runtime과 기존 Audit을 변경하지 않는다.

## 7. Phase 18-C 격리 Safety Validation

`IsolatedEmergencyReturnSafetyValidator`는 각 후보마다 T+240 Traffic State Map을 새로 복사한다. Speed와
Entry Delay만 복사본의 운동학적 State에 반영하고 Sequence Change는 논리 순서 증거로 평가한다. 검증 후
원 Traffic, Clock, Queue, Candidate와 Aircraft Runtime은 모두 불변이다.

`POC_EMERGENCY_RETURN_SAFETY_V1`의 잠정 Gate는 다음과 같다.

- Look-ahead 120초에서 T+240 기준선에 없던 새로운 `PREDICTED` Conflict가 없어야 한다.
- Speed 변화는 Performance 범위와 50 kt 이내, Entry Delay는 60초 이내여야 한다.
- `MIL-T01`은 Arrival Sequence 2번 이내여야 한다.
- 이미 안정된 `CIV-A01`은 첫 순서를 유지해야 한다.
- No-action 기준선은 비교 증거이며 `SAFE`가 될 수 없다.

기준선의 `CIV-A03 / MIL-F01` Conflict는 기존 Exception으로 계속 관리되므로 비상 후보가 새로 만든
Conflict로 중복 판정하지 않는다. 대신 Validation Run에 `baseline_conflict_aircraft_ids`로 명시한다.
이 비회귀 기준은 전체 Traffic이 Conflict-free라는 뜻이 아니다.

| 후보 | 새 Conflict | 성능 | Priority/안정 접근 | Verdict |
|---|---:|---|---|---|
| `ER-CAND-A` | 0 | PASS | 2번 / 보존 | `SAFE` |
| `ER-CAND-B` | 0 | PASS | 2번 / 보존 | `SAFE` |
| `ER-CAND-C` | 0 | PASS | 1번 / `CIV-A01` 이동 | `UNSAFE` |
| `ER-CAND-D` | 0 | PASS | 5번 / No-action | `UNSAFE` |

Session과 Web UI는 120초 검증 Profile, 기준선 Conflict, 후보별 Verdict·Reason Code·Gate 증거를 표시한다.
`SAFE`는 후속 추천 대상으로 사용할 수 있다는 의미일 뿐이며 `NOT APPLIED` 상태를 유지한다.

## 8. Phase 18-D 비상 복귀 추천 순위

`DeterministicEmergencyReturnRecommendationRankingService`는 Phase 18-C의 완전한 Validation Run에서
`SAFE`인 Action Candidate만 선택한다. No-action 기준선과 `UNSAFE` 후보는 추천할 수 없다. 선택한 후보는
다음 키를 오름차순으로 비교하며 Candidate ID는 동률 해소에만 사용한다.

1. `operational_cost_score`
2. `estimated_delay_seconds`
3. `estimated_path_extension_nm`
4. `candidate_id`

Golden Demo에서는 `ER-CAND-B`가 비용 5·지연 0초로 1순위, `ER-CAND-A`가 비용 20·지연 30초로
2순위다. C와 D는 순위가 없다. 각 Recommendation은 원 Candidate와 Validation Result를 그대로 연결하고
안전 Gate, 순서, 비용과 `Controller decision required; not applied` 설명을 보존한다.

Session API는 Recommendation Set ID, Ranking Policy ID, Availability, Primary Candidate ID와 후보별
Rank·설명을 기존 `emergency_return_candidates` 증거 묶음에 추가한다. UI의 Primary 표시는 실행 명령이
아니며, 추천 조회 전후 Clock·Traffic·Queue·Audit과 Aircraft Runtime은 동일해야 한다.

## 9. Phase 18-E 비상 복귀 결정과 Audit

T+240 `EMERGENCY_DECLARED`에서 관제사는 추천 묶음당 한 번만 다음 결정을 내릴 수 있다.

- `ACCEPT_EMERGENCY_RETURN`: 1순위 `ER-CAND-B`를 선택하고 적용 권한을 기록한다.
- `MODIFY_EMERGENCY_RETURN`: 이미 `SAFE`인 비주 후보(데모에서는 `ER-CAND-A`)와 사유를 기록하며,
  적용 전에 다시 검증해야 하는 상태로 둔다.
- `REJECT_EMERGENCY_RETURN`: 거절 사유를 기록하고 선택 후보를 두지 않는다.

Audit은 원 추천·선택 추천/후보, 관제 위치, UTC 결정 시각, 사유, 적용 권한과 재검증 필요 여부를
보존한다. 세 결정 모두 T+240 Clock, Traffic, Exception Queue 및 Aircraft Runtime을 변경하지 않으며
응답의 `applied`는 항상 `false`다. Reset은 이 process-local Audit도 함께 제거한다.

## 10. Phase 18-F 적용과 회복

`APPLY_EMERGENCY_RETURN`은 T+240의 `EMERGENCY_DECISION_ACCEPTED` 또는
`EMERGENCY_DECISION_MODIFIED`에서만 실행된다. 선택된 후보가 같은 Recommendation Set에서 다시
`SAFE`로 검증되는지 확인한 뒤 Action을 실제 Synthetic Runtime에 적용한다. Accept는 `ER-CAND-B`,
Modify는 선택한 `ER-CAND-A`의 세 Action을 적용한다. Reject에는 적용 경로가 없다.

적용 직후 Clock은 T+240에 머물며 Stage는 `EMERGENCY_RETURN_APPLIED`다. 따라서 브라우저는 T+240부터
T+260까지 Marker와 Trail을 연속 재생할 수 있다. T+260 `RECOVERY_COMPLETE` Cue에서
`COMPLETE_EMERGENCY_RECOVERY`가 실행되면 `MIL-T01`은 `NONE / FINAL`, Operational Priority는
`ROUTINE`, 해당 Priority Exception은 `RESOLVED`가 된다.

`recovery_complete`는 비상 항공기와 그 Queue 항목의 회복만 뜻한다. T+260에도 존재하는 별도
`CIV-A03 / MIL-F01` HIGH Conflict는 삭제하거나 안전으로 위장하지 않고
`remaining_high_critical_pairs`에 보존한다. 전체 시나리오의 `SC-014` 충족은 Phase 19의 종료 평가와
추가 조정 범위다.
