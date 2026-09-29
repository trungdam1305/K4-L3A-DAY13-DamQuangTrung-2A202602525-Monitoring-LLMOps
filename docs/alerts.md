# Alert và Runbook

Mỗi alert dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ. Rule nằm tại [`config/alert_rules.yaml`](../config/alert_rules.yaml); SLO và error budget tại [`config/slo.yaml`](../config/slo.yaml). Mọi bước kiểm tra đi theo thứ tự **Metrics → Logs → Traces**.

Lệnh dùng chung (chạy từ thư mục gốc repo):

```bash
python scripts/build_dashboard.py                      # số liệu 60 phút gần nhất + dashboard PNG
curl -s http://127.0.0.1:8000/metrics                  # snapshot in-memory của API
curl -s http://127.0.0.1:8000/health                   # trạng thái incident toggles
```

## Alert 1

- **Tên:** `chat_latency_p95_slo_breach`
- **Severity:** P2-high
- **Duration:** 5m
- **Kênh thông báo:** Slack `#day13-k4-l3a-oncall`
- **SLI/SLO liên quan:** `fast_successful_requests` — 99.5% request trả lời thành công trong ≤ 2000 ms, cửa sổ 28 ngày (error budget 0.5%).
- **Điều kiện và thời gian duy trì:** `p95(response_sent.latency_ms) > 2000` trên cửa sổ trượt 5 phút, duy trì liên tục 5 phút. Một đợt chậm ngắn (một request cold start) không kích hoạt; sự cố kéo dài thì có.
- **Ảnh hưởng tới người dùng:** câu trả lời chậm hơn 2 giây; mọi request chậm đều tiêu error budget. Ở baseline P95 ≈ 160 ms nên vượt 2000 ms là suy giảm > 10x.
- **Ba bước kiểm tra đầu tiên:**
  1. *Metrics:* mở panel **Latency percentiles and TTFT**. Nếu P95/P99 tăng nhưng **TTFT P95 không đổi** (~50 ms) thì LLM vẫn trả token đầu nhanh, nghĩa là thời gian bị tiêu ở bước khác (retrieval, queue). Ghi lại khoảng thời gian bắt đầu tăng.
  2. *Logs:* lọc request chậm trong khoảng đó và lấy `correlation_id`, `trace_id`:
     ```bash
     python -c "import json;[print(r['ts'],r['correlation_id'],r['feature'],r['latency_ms'],r.get('trace_id')) for r in map(json.loads,open('data/logs.jsonl',encoding='utf-8')) if r.get('event')=='response_sent' and r.get('latency_ms',0)>2000]"
     ```
     Kiểm tra request chậm có tập trung vào một `feature`/`model` không.
  3. *Traces:* mở trace theo `trace_id` (hoặc filter metadata `correlation_id` trong Langfuse). So sánh duration của `rag-retrieval` và `llm-generation` dưới root `lab-agent-run`; span chiếm phần lớn duration là nơi khoanh vùng.
- **Mitigation tạm thời:** nếu `rag-retrieval` chậm: bật timeout/circuit breaker cho retrieval, trả lời bằng fallback docs hoặc cache; nếu `llm-generation` chậm: chuyển sang model nhỏ hơn hoặc giảm `max_tokens`; nếu cả hai bình thường mà latency client cao: kiểm tra queue/concurrency (endpoint đang chạy sync trong event loop). Sau khi xử lý, xác nhận P95 < 2000 ms trong 15 phút rồi mới đóng alert.
- **Owner:** AI platform on-call (Dam Quang Trung)

## Alert 2

- **Tên:** `chat_error_rate_high`
- **Severity:** P1-critical
- **Duration:** 2m
- **Kênh thông báo:** Slack `#day13-k4-l3a-oncall`
- **SLI/SLO liên quan:** `fast_successful_requests` (mỗi `request_failed` là một bad event) và guardrail `error_rate_pct_max = 2`, `retrieval_success_rate_pct_min = 90`.
- **Điều kiện và thời gian duy trì:** `count(request_failed) / count(request_received) * 100 > 2` trên cửa sổ trượt 5 phút, **và** có ít nhất 10 request trong cửa sổ (tránh báo động giả khi traffic quá thấp), duy trì 2 phút. Duration ngắn vì người dùng nhận HTTP 500 ngay lập tức; error rate 100% có thể đốt hết error budget 28 ngày trong vài giờ.
- **Ảnh hưởng tới người dùng:** người dùng nhận lỗi 500, không có câu trả lời.
- **Ba bước kiểm tra đầu tiên:**
  1. *Metrics:* mở panel **Error rate and retrieval success**. Xem breakdown `error_type` và retrieval success có giảm cùng lúc không. Error rate tăng song song với retrieval success giảm thì lỗi nằm ở bước retrieval.
  2. *Logs:* lọc `event == "request_failed"`; đọc `error_type`, `tool_name`, `tool_success`, `payload.detail` và lấy `correlation_id` (load test cũng in ID này từ header `x-request-id`):
     ```bash
     python -c "import json;[print(r['ts'],r['correlation_id'],r.get('error_type'),r.get('tool_name'),r.get('payload',{}).get('detail')) for r in map(json.loads,open('data/logs.jsonl',encoding='utf-8')) if r.get('event')=='request_failed']"
     ```
  3. *Traces:* trong Langfuse, filter metadata `correlation_id`, hoặc filter level `ERROR` quanh cùng timestamp. Tìm observation có level ERROR (ví dụ `rag-retrieval` có status message `Vector store timeout`) và kiểm tra `llm-generation` có được gọi hay không.
- **Mitigation tạm thời:** nếu lỗi do retrieval: degrade gracefully (trả lời bằng fallback docs và gắn cờ `tool_success=false` thay vì 500), retry có backoff, kiểm tra vector store; nếu lỗi sau khi đổi prompt/model: rollback label `production` về version trước trong Langfuse. Thông báo trạng thái trên Slack mỗi 15 phút cho tới khi error rate < 2%.
- **Owner:** AI platform on-call (Dam Quang Trung)

## Alert 3

- **Tên:** `chat_cost_per_request_spike`
- **Severity:** P3-warning
- **Duration:** 15m
- **Kênh thông báo:** Slack `#day13-k4-l3a-finops`
- **SLI/SLO liên quan:** guardrail `daily_cost_usd_max = 2.5` và panel cost/tokens (`total ≤ 2.5 USD`, `sum_by_field ≤ 50000 tokens` trong 60 phút).
- **Điều kiện và thời gian duy trì:** `avg(response_sent.cost_usd) > 0.005` trên cửa sổ 15 phút (baseline ≈ 0.0021 USD/request, tức vượt khoảng 2.4x) **hoặc** `sum(cost_usd)` trong 60 phút > 2.5 USD; duy trì 15 phút. Dùng cost *mỗi request* thay vì tổng để không báo nhầm khi traffic tăng hợp lệ.
- **Ảnh hưởng tới người dùng:** không lỗi trực tiếp, nhưng câu trả lời dài bất thường và ngân sách bị đốt nhanh; nếu kéo dài sẽ chạm hạn mức chi phí và buộc phải rate-limit người dùng.
- **Ba bước kiểm tra đầu tiên:**
  1. *Metrics:* so sánh panel **Cost over time** và **Input and output tokens** với **Request traffic**. Cost tăng mà traffic không tăng nghĩa là cost/request tăng. Xác định field tăng: `tokens_out` (câu trả lời dài) hay `tokens_in` (prompt/context phình).
  2. *Logs:* lọc `response_sent` có `cost_usd` hoặc `tokens_out` cao và lấy `correlation_id`; kiểm tra có tập trung vào một `feature`/`model` không.
  3. *Traces:* mở generation `llm-generation` của các request đó; xem `usage` (input/output), `cost` và prompt name/version/label. Nếu request cost cao đều dùng một prompt version mới thì nhiều khả năng prompt đó là nguyên nhân.
- **Mitigation tạm thời:** đặt/giảm `max_tokens` cho output; rollback label `production` về prompt version trước nếu version mới làm câu trả lời dài hơn; chuyển feature ít quan trọng sang model rẻ hơn; bật cache cho câu hỏi lặp lại.
- **Owner:** LLMOps / FinOps owner (Dam Quang Trung)
