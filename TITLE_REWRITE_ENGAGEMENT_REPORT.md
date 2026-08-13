# Title Rewrite Engagement Report

## Kết quả

Title Rewrite giữ nguyên kiến trúc tại commit nền `e3ada98`: cùng persistent Qwen worker, text-only, cùng artifact, sanitizer, collision policy và `<filename_base>_PART_N.mp4`. Không đổi selector, semantic cleaner, Silence Cutter, formatter hay renderer.

Prompt cũ ưu tiên title “clean”, nên thường rút title thành nhãn chủ đề như `50 Dollar Tree Deals`. Prompt mới ưu tiên title tự nhiên, trung thực nhưng phải giữ lý do nhấp: curiosity, stakes, benefit, surprise, contrast, consequence, open loop, specificity hoặc personal transformation. Model nhận biết nội bộ LIST/QUESTION/HOW_TO/WARNING/PERSONAL_STORY/CONTRARIAN/MONEY/COMPARISON/REVEAL; output vẫn chỉ:

```json
{"rewritten_title":"..."}
```

Prompt cấm dịch, bịa facts/numbers/results/danger/controversy/urgency, spam ALL CAPS, emoji, markdown và spam punctuation. `max_new_tokens=32`, deterministic generation, không frame.

## Guard và retry

Guard nhẹ kiểm tra: JSON/title rỗng, generic English topic 1–3 từ, mất hook rõ ràng, model explanation, markdown, trên 100 ký tự, số mới không có trong nguồn, bỏ toàn bộ số nguồn, uppercase quá mức, `!!`/`??` và thay đổi script/ngôn ngữ rõ ràng của tiếng Việt/CJK/Japanese/Korean/Cyrillic. Output lỗi được corrective retry đúng một lần. Worker/network failure không retry title; fallback ngay title gốc sanitize. Artifact thành công/fallback đều được tái dùng khi restart.

Normal path dùng 1 generation. Acceptance có 21 corrective retries, chủ yếu vì Qwen bọc JSON trong markdown lần đầu; không có retry loop. Timeout generation riêng title tăng từ 10 lên 30 giây vì đo thật thường vượt 10 giây; vẫn override được bằng `TITLE_REWRITE_TIMEOUT`. Health probe tối đa 0,25 giây và không chờ worker khởi động: kiến trúc worker luôn ấm cho phép fallback ngay, tránh chặn batch khi worker tắt.

## Warm Qwen acceptance

Fixture: 32 title, gồm retirement, personal finance, grocery/frugal, travel/expat, lifestyle, housing, list, warning, question, personal story, tiếng Việt và tiếng Trung. Chi tiết máy đọc: `TITLE_REWRITE_QUALITY_ACCEPTANCE.json`.

- Worker trước: READY, model load count 1, request count 53.
- Worker sau: READY, model load count 1, request count 106.
- APPLIED: 29.
- FALLBACK: 3.
- Retry: 21.
- Total generations: 53.
- Average latency/title: 27,943 giây.
- Median: 30,637 giây.
- Max: 44,713 giây.
- Additional model load: 0.

Representative results:

| Original | Rewritten |
|---|---|
| I Live Alone in Retirement and It's a Game Changer! | Why Living Alone in Retirement Changed Everything |
| Why Leasing a Car in Retirement ACTUALLY Works | Why Leasing a Car in Retirement Might Actually Make Sense |
| Gen X is in BIG Financial Trouble - Here's Why | Why Gen X Faces Significant Financial Challenges |
| Can You Really Retire With Only $300,000? | Is $300,000 Enough for Retirement? |
| 50 *NEW* Dollar Tree Deals you NEED to buy! (from the pro!) | 50 Dollar Tree Finds Actually Worth Buying |
| Zero Dollars Left for Food? Here's What I Actually Eat | What I Really Eat When Money Runs Out |
| THIS is How Much it Costs to Build a House in Colombia | What It Really Costs to Build a House in Colombia |
| 12 Amazon Products I Use Every Single Day | 12 Amazon Items I Can't Live Without |
| I Lost My Job at 55 and Had to Start Over | My Life Changed After Losing My Job at 55 |
| Gia đình 4 người chi tiêu thế nào với 5 triệu mỗi tháng? | Gia đình 4 người tiết kiệm 5 triệu mỗi tháng như thế nào? |

Ba FALLBACK an toàn:

- `7 Things Nobody Tells You About Retiring Early`: retry bỏ số 7, guard từ chối; giữ title gốc.
- `Tôi sống một mình 30 ngày và điều này đã thay đổi tôi`: model dịch sang English ở cả hai lượt; guard từ chối, giữ tiếng Việt gốc.
- `移居日本前一定要知道的7件事`: model đổi sang English; guard từ chối, giữ title CJK gốc.

## Quality review

Title còn quá trung tính, cần theo dõi ở benchmark sau:

- #12 `Affordability of Colombia for Expats in 2026`.
- #13 `My Journey of Quitting Social Media for 30 Days`.

Title có engagement nhưng hơi công thức/sensational:

- #22–23 dùng `Discover...`.
- #25 và #27 thêm `Surprising/Surprisingly`; nguồn có question/open loop nhưng không khẳng định surprise.
- #29 thêm `Life-Changing`.

Không thêm scorer chủ quan để tự chặn các trường hợp trên; yêu cầu chỉ dùng guard cấu trúc bảo thủ. Acceptance JSON giữ toàn bộ 32 cặp để review thủ công.

## Regression

- Full suite cuối: `355 passed, 1 skipped, 111 subtests passed in 43.06s`.
- Focused title/worker/formatter/flow/bridge/job runner: `111 passed, 40 subtests passed in 8.34s`.
- Scheduler 40 URL + title tests sau fail-fast health: `14 passed, 12 subtests passed in 23.09s`.
- `compileall`: PASS.
- `git diff --check`: PASS.
