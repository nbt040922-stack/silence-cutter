# ContentOps Monitor — Jobs Sidebar Tree Correction

## Delivered

- Restored the expandable Jobs tree in the polished sidebar.
- Hierarchy is now: Dashboard, Kênh theo dõi, Jobs, Tất cả, Đang chạy, Chờ xử lý, Đã hoàn thành, Thất bại, Tạo Job mới, Services, Cảnh báo, Nhật ký, Cấu hình.
- Jobs has a right-side chevron and children are visibly indented.
- Tạo Job mới is a Jobs child; AUTO/MANUAL remain inside the Jobs page toolbar.
- Child selection preserves the existing Jobs filters and Manual workspace behavior.
- Backend/API contracts and contextual Jobs actions were not changed.

## Live visual QA

- Jobs expanded: PASS; all six child entries visible.
- Jobs collapsed: PASS; all six child entries hidden and chevron changes direction.
- Tất cả selected: PASS; Jobs page opens with Unified jobs.
- Tạo Job mới selected: PASS; Manual job creation workspace opens and child receives the active state.

## Verification

- Build: passed, 0 warnings, 0 errors.
- Tests: passed, 11/11.
- `git diff --check`: passed.
- No commit or push performed.
