# Golden Demo Emergency Session Contract

## 1. 범위

Phase 18-A는 해결된 Golden Demo Run을 T+90에서 T+240으로 진행하고, 기존 Synthetic
`EVT-MIL-T01-EMERGENCY`를 Session·Exception Queue·Web UI에 동일한 Checkpoint로 노출한다.
비상 복귀 후보 생성과 관제사 결정·적용은 이 단계의 범위가 아니다.

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
