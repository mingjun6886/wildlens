# WildLens — Bản đặc tả thiết kế

**Ngày:** 2026-09-22
**Trạng thái:** Đã duyệt, sẵn sàng lập kế hoạch triển khai
**Tác giả:** Minh Quan Nguyen

---

## 1. Mục tiêu

Dựng lại solo một nền tảng nhận diện động vật hoang dã serverless, làm sản phẩm trưng bày trong hồ sơ xin việc.

**Bối cảnh:** Hệ thống này bắt nguồn từ một bài tập nhóm 4 người ở đại học (FIT5225, Monash). Bản dựng lại này được viết lại từ đầu bởi một người, trên tài khoản AWS cá nhân, với những thay đổi kiến trúc có chủ đích. README công khai sẽ ghi rõ nguồn gốc này.

**Đích nghề nhắm tới:** Cloud / DevOps / Platform Engineer, kết hợp ML Engineer. Thiết kế vì vậy ưu tiên: hạ tầng bằng code, CI/CD, khả năng quan sát, xử lý lỗi, kiểm soát chi phí, giảm độ trễ, quản lý phiên bản mô hình, và đo đạc độ chính xác của mô hình.

**Ngân sách:** $100 credit AWS. Không được vượt.

### Tiêu chí thành công

| # | Tiêu chí | Cách đo |
|---|---|---|
| S1 | Toàn bộ hạ tầng dựng và xoá được bằng một lệnh | `terraform apply` / `terraform destroy` chạy sạch từ số không |
| S2 | Có con số cải thiện độ trễ thật | Đo p95 khởi động nguội trước và sau khi tối ưu, ghi vào README |
| S3 | Có con số độ chính xác mô hình thật | Bộ chấm điểm chạy trên 26 ảnh test, xuất báo cáo từng loài |
| S4 | Chi phí có trần cứng | Không kịch bản lỗi nào vượt được $5/ngày |
| S5 | Sự cố có thể chẩn đoán được | Một mã lần chạy lần ra được toàn bộ hành trình của một file |
| S6 | Không có bí mật dài hạn nào trong repo | CI dùng OIDC, không có khoá truy cập AWS được lưu |

### Ngoài phạm vi

- Nhiều môi trường (chỉ làm `dev`)
- Xác thực phân quyền theo vai trò — mọi người dùng đã đăng nhập đều bình đẳng
- Giao diện quản lý sửa nhãn / xoá hàng loạt (API xoá tối giản vẫn có, để dọn dữ liệu test)
- Tối ưu chi phí cho quy mô sản xuất thật (ví dụ chuyển sang chỉ mục thay vì quét toàn bảng)

---

## 2. Quyết định kiến trúc

Sáu quyết định dưới đây là những chỗ bản này **khác** bài tập gốc. Mỗi quyết định sẽ có một file ADR tương ứng.

| # | Quyết định | Lý do |
|---|---|---|
| AD-1 | Hạ tầng bằng **Terraform**, không bấm chuột | Một công cụ quản được cả AWS lẫn GCP. `destroy` bảo vệ credit. Được nhắc tới nhiều nhất trong tin tuyển dụng |
| AD-2 | Vùng **ap-southeast-2** (Sydney) | Gần người dùng; bài gốc dùng us-east-1 chỉ vì lớp học bắt buộc |
| AD-3 | **SQS + DLQ** giữa S3 và Lambda xử lý | Trần chi phí cứng, thử lại có kiểm soát, file hỏng không bị mất |
| AD-4 | Mô hình **nhúng vào image, cho phép ghi đè từ S3** | Khởi động nguội nhanh mà vẫn giữ được khả năng đổi mô hình không cần sửa code |
| AD-5 | **Không dùng VPC** | Lambda trong VPC cần NAT Gateway: $32/tháng tính theo giờ, đổi lấy con số không. Mọi dịch vụ được gọi đều là điểm cuối công khai đã có IAM bảo vệ |
| AD-6 | **Bản ghi trạng thái ghi sớm** (PENDING → DONE) | Cho phép giao diện hiện tiến trình thay vì treo 3 phút |

---

## 3. Kiến trúc

### 3.1 Sơ đồ thành phần

```
        ┌──────────────────────────────┐
        │  CloudFront + S3 (web tĩnh)  │
        └──────────────┬───────────────┘
                       │  JWT ở header Authorization
                       ▼
        ┌──────────────────────┐     ┌──────────────┐
        │   API Gateway (REST) │◄────┤   Cognito    │
        │   Cognito Authorizer │     │  User Pool   │
        └──────────┬───────────┘     └──────────────┘
                   │
      ┌────────────┴─────────────┐
      │  Lambda zip (Python 3.11)│
      │  upload · search · status│
      │  delete                  │
      └────────────┬─────────────┘
                   │
   ┌───────────────┼──────────────────────────────────┐
   │               ▼                                  ▼
   │       ┌──────────────┐                  ┌────────────────┐
   │       │  S3 kho thô  │                  │   DynamoDB     │
   │       └──────┬───────┘                  │ wildlens-files │
   │              │ ObjectCreated            └────────▲───────┘
   │              ▼                                   │
   │       ┌──────────────┐   hỏng 3 lần   ┌────────┐ │
   │       │     SQS      │───────────────►│  DLQ   │ │
   │       └──────┬───────┘                └────┬───┘ │
   │              │ trần đồng thời = 2          │     │
   │              ▼                          cảnh báo │
   │    ┌─────────────────────┐                 │     │
   │    │ Lambda container AI │─────────────────┼─────┘
   │    │ 4 GB · 900 s        │                 │
   │    │ MegaDetector→       │                 ▼
   │    │ SpeciesNet          │           ┌──────────┐
   │    └──────┬───────┬──────┘           │   SNS    │
   │           │       │                  └──────────┘
   │           ▼       ▼
   │   ┌───────────┐ ┌──────────┐
   │   │S3 ảnh nhỏ │ │   ECR    │
   │   └───────────┘ └──────────┘
   │
   └── Quan sát: X-Ray · CloudWatch Dashboard · Alarms · log JSON giữ 14 ngày

   ☁️ GCP Cloud Run: điểm cuối /resolve, tự xác minh JWT do Cognito cấp
```

### 3.2 Tài nguyên AWS

| Tài nguyên | Tên | Ghi chú |
|---|---|---|
| S3 | `wildlens-raw-<hậu tố>` | Ảnh/video gốc. Chặn truy cập công khai. Luật dọn: xoá sau 30 ngày |
| S3 | `wildlens-thumb-<hậu tố>` | Ảnh thu nhỏ |
| S3 | `wildlens-models-<hậu tố>` | Mô hình để ghi đè phiên bản; không phải đường nạp mặc định |
| S3 | `wildlens-web-<hậu tố>` | Web tĩnh, phục vụ qua CloudFront |
| DynamoDB | `wildlens-files` | Khoá chính `fileId`. Chế độ theo lượt dùng. Bật TTL trên trường `ttl` |
| SQS | `wildlens-ingest` | Thời gian ẩn phiếu 5400 s. maxReceiveCount = 3 |
| SQS | `wildlens-ingest-dlq` | Không nối với Lambda nào. Giữ phiếu 14 ngày |
| Lambda | `wildlens-process` | Ảnh container. 4096 MB, 900 s, `/tmp` 4096 MB, x86_64, đồng thời tối đa 2 |
| Lambda | `wildlens-upload` / `-search` / `-status` / `-delete` / `-subscribe` | Zip, Python 3.11, 512 MB |
| ECR | `wildlens-process` | Luật dọn: giữ 3 ảnh gần nhất |
| Cognito | `wildlens-users` | Email + họ tên, xác thực email, Hosted UI |
| API Gateway | REST | Cognito Authorizer trên **mọi** route |
| SNS | `wildlens-tags` | Đăng ký email kèm FilterPolicy theo loài |
| CloudWatch | Log group cho mỗi Lambda | **Giữ 14 ngày, khai báo tường minh** |

Hậu tố là một chuỗi ngẫu nhiên 6 ký tự do Terraform sinh, vì tên bucket S3 phải duy nhất toàn cầu.

---

## 4. Mô hình dữ liệu

### 4.1 Bản ghi DynamoDB

```json
{
  "fileId":       "a3f9c2… (SHA-256 hex, khoá chính)",
  "status":       "PENDING | PROCESSING | DONE | FAILED",
  "type":         "image | video",
  "s3Key":        "a3f9c2….jpg",
  "thumbKey":     "a3f9c2….jpg | null",
  "tags":         { "wild boar": 1 },
  "uploadedBy":   "nguoi@vidu.com",
  "createdAt":    1758499200,

  "modelVersion": "v1",
  "modelLoadMs":  410,
  "processingMs": 2180,
  "coldStart":    false,
  "frameCount":   null,
  "errorReason":  null,
  "ttl":          1758502800
}
```

**Quy ước về các trường:**

- `fileId` là SHA-256 của **nội dung** file, không phải tên file. Nó vừa là khoá chính, vừa là khoá đối tượng trên S3, vừa là cơ chế chống trùng.
- `s3Key` / `thumbKey` lưu khoá đối tượng, **không lưu URL**. URL được ký lúc đọc. Bài gốc lưu URL đầy đủ và phải cắt chuỗi để lấy lại khoá — cách đó dễ vỡ.
- `ttl` chỉ có giá trị khi `status = PENDING`. Chuyển sang `DONE` thì trường này bị **gỡ bỏ** (dùng `REMOVE` trong biểu thức cập nhật), khiến bản ghi sống vĩnh viễn.
- `tags` rỗng `{}` là kết quả hợp lệ (ảnh không có con vật nào), khác với `null`.
- `frameCount` chỉ có giá trị với video.

### 4.2 Luồng chuyển trạng thái

| Từ | Sang | Ai ghi | Điều kiện |
|---|---|---|---|
| — | `PENDING` | `wildlens-upload` | `attribute_not_exists(fileId)` |
| `PENDING` | `PROCESSING` | `wildlens-process` | `status = PENDING` |
| `PROCESSING` | `DONE` | `wildlens-process` | `status <> DONE` ← **chốt chống trùng** |
| `PROCESSING` | `FAILED` | `wildlens-process` | lỗi vĩnh viễn, hoặc hết lượt thử |

Điều kiện ở hàng thứ ba là cơ chế đảm bảo tính bất biến khi lặp. SQS loại thường giao *ít nhất một lần*; nếu một phiếu được giao hai lần, lần ghi thứ hai bị DynamoDB từ chối với `ConditionalCheckFailedException`. Lambda bắt lỗi này, ghi log ở mức INFO, và kết thúc thành công — **không** ném lỗi, vì ném lỗi sẽ khiến phiếu quay lại hàng đợi.

---

## 5. Hợp đồng API

Tất cả route đều nằm sau Cognito Authorizer. Tất cả phản hồi đều là JSON.

| Route | Thân yêu cầu | Trả về |
|---|---|---|
| `POST /upload` | `{sha256, ext, size}` | `{duplicate: bool, uploadUrl?, key?}` |
| `GET /files/{fileId}` | — | `{status, tags?, thumbUrl?, fullUrl?, errorReason?}` |
| `POST /search/tags` | `{"wombat": 2, "magpie": 1}` | `{results: [{fileId, thumbUrl, fullUrl, tags}]}` |
| `POST /search/species` | `["dingo", "koala"]` | như trên |
| `POST /search/byfile` | `{file: "<base64>", ext}` | như trên |
| `POST /subscribe` | `{email, tag, operation}` | `{ok, tags}` |
| `POST /files/delete` | `{fileIds: [...]}` | `{ok, deleted}` |
| `POST /resolve` *(GCP)* | `{thumbUrl}` | `{fullUrl}` |

**Ngữ nghĩa tìm kiếm:**
- `/search/tags` — phép **VÀ** logic kèm số lượng tối thiểu. Một file khớp khi với mọi cặp `(loài, n)` trong yêu cầu, file đó có `tags[loài] >= n`.
- `/search/species` — phép **HOẶC**. Một file khớp khi chứa **bất kỳ** loài nào trong danh sách, số lượng ≥ 1.
- `/search/byfile` — chạy nhận diện trên file gửi lên, rồi trả về các file có tập nhãn **bao hàm** tập nhãn của file truy vấn. **File truy vấn tuyệt đối không được lưu lại.**

`/search/byfile` do `wildlens-search` phục vụ, nhưng nó **không tự nạp mô hình**. Nó gọi đồng bộ `wildlens-process` với payload `{"query_mode": true, "file_bytes": "<base64>", "ext": "jpg"}`; Lambda xử lý trả về `{"tags": {...}}` mà không ghi gì vào DynamoDB, S3 hay SNS. Nhờ vậy chỉ một Lambda duy nhất trong hệ thống phải mang theo mô hình.

Mọi truy vấn đều quét toàn bảng rồi lọc trong bộ nhớ. Đây là lựa chọn có chủ đích ở quy mô này (dưới vài nghìn bản ghi) và sẽ được ghi vào ADR kèm điều kiện kích hoạt việc chuyển sang chỉ mục.

---

## 6. Pipeline ML

### 6.1 Hai tầng

1. **MegaDetector v5a** — nhận cả tấm ảnh, trả về các khung bao kèm nhãn lớp: `1` = động vật, `2` = người, `3` = phương tiện. Chỉ xử lý lớp `1`, ngưỡng tin cậy ≥ 0.05.
2. **SpeciesNet** — nhận từng khung đã cắt, phóng lên 600×600 rồi đưa về 480×480, **hoán vị sang bố cục kênh-cuối `(B,H,W,C)`**, trả về logit cho 46 lớp. Lấy lớp có xác suất cao nhất.
3. **Ánh xạ nhãn** — `labels.txt` đổi tên khoa học (`Sus_scrofa`) sang tên thường (`wild boar`). Các cột dùng là 4, 5, 6 (chi, loài, tên thường).

Danh sách `CLASSES` gồm 46 phần tử; **thứ tự phải khớp với thứ tự đầu ra của mô hình** và không được sắp xếp lại.

### 6.2 Nạp mô hình

```
Mặc định:   /opt/models/v1/{mdv5a.pt, model.pt, labels.txt}   ← nhúng trong ảnh container
Ghi đè:     s3://wildlens-models-<hậu tố>/<MODEL_VERSION>/     ← khi MODEL_VERSION ≠ "v1"
```

`model_loader.get_models(version)` trả về từ bộ nhớ đệm nếu đã nạp; nếu không thì nạp từ đĩa (mặc định) hoặc tải từ S3 (ghi đè). Bộ nhớ đệm có khoá là chuỗi phiên bản, nên một môi trường thực thi ấm có thể giữ nhiều phiên bản cùng lúc.

Việc này giữ lại khả năng đổi mô hình mà không sửa code (vặn biến môi trường, không build lại ảnh) đồng thời loại bỏ 470 MB tải về ở mỗi lần khởi động nguội.

### 6.3 Xử lý video

Trích một khung mỗi giây, **tối đa 60 khung**. Gắn nhãn từng khung, cộng dồn số lượng theo loài. Ảnh thu nhỏ lấy từ khung đầu tiên. Video dài hơn 60 giây bị cắt bớt, và `frameCount` ghi lại số khung thực sự đã xử lý.

### 6.4 Chấm điểm độ chính xác

`ml/eval/run_eval.py` chạy pipeline trên 26 ảnh test, so với `ml/eval/answer_key.yaml`, xuất ra:

- Độ chính xác (precision), độ bao phủ (recall), điểm F1 cho từng loài
- Ma trận nhầm lẫn giữa các loài
- Sai lệch về số lượng cá thể (đếm thừa / đếm thiếu)
- Thời gian xử lý trung bình mỗi ảnh

Kết quả ghi vào `ml/eval/reports/<phiên bản>.md` và được commit. Khi đụng vào code ML, CI chạy lại và dán bảng so sánh vào bình luận Pull Request.

**Phép kiểm chuẩn:** `Sus_scrofa_1.JPG` phải ra đúng `{"wild boar": 1}`.

---

## 7. Xử lý lỗi

### 7.1 Phân loại lỗi

| Loại | Ví dụ | Xử lý |
|---|---|---|
| **Tạm thời** | S3 quá tải, mạng chập, DynamoDB throttle | Ném lỗi → SQS thử lại (tối đa 3) |
| **Vĩnh viễn** | File vỡ, định dạng không hỗ trợ, ảnh 0 byte | Ghi `FAILED` kèm `errorReason`, **xoá phiếu, không thử lại** |
| **Quá sức** | Video quá dài | Cắt bớt ở 60 khung, coi là thành công, ghi `frameCount` |
| **Trùng lặp** | Phiếu được giao hai lần | Bắt `ConditionalCheckFailedException`, log INFO, thoát êm |

Phân biệt tạm thời với vĩnh viễn là điểm cốt lõi: thử lại một file vỡ ba lần là trả tiền ba lần cho một việc chắc chắn hỏng.

### 7.2 Cấu hình hàng đợi

| Tham số | Giá trị | Lý do |
|---|---|---|
| Thời gian ẩn phiếu | 5400 s | 6 × thời gian chạy tối đa của Lambda (900 s) |
| `maxReceiveCount` | 3 | Ba lần thất bại là đủ để kết luận |
| Giữ phiếu ở DLQ | 14 ngày | Tối đa SQS cho phép; đủ thời gian khám nghiệm |
| Kích thước lô | 1 | Một file mỗi lần chạy; đơn giản hoá tính bất biến khi lặp |
| Đồng thời dành riêng | 2 | Trần chi phí cứng |

### 7.3 Cảnh báo

| Cảnh báo | Ngưỡng | Hành động |
|---|---|---|
| DLQ có phiếu | `ApproximateNumberOfMessagesVisible >= 1` | Email → mở `runbook.md` |
| Lambda lỗi | > 3 lỗi trong 5 phút | Email |
| Bản ghi kẹt ở PENDING | > 10 bản ghi cũ hơn 15 phút | Email |
| Ngân sách | $10 / $25 / $50 | Email từ AWS Budgets |

---

## 8. Khả năng quan sát

**Log có cấu trúc.** Mọi log là JSON một dòng, kèm `fileId` làm mã tương quan. Một câu truy vấn CloudWatch Logs Insights lần ra được toàn bộ hành trình của một file qua mọi Lambda.

**Truy vết phân tán.** Bật X-Ray trên API Gateway và tất cả Lambda. Các đoạn con được đánh dấu quanh: nạp mô hình, MegaDetector, SpeciesNet, tạo ảnh thu nhỏ, ghi DynamoDB. Nhờ đó trả lời được *"40 giây đó tiêu vào đâu"* mà không cần đoán.

**Chỉ số tự định nghĩa** (giới hạn 4 để kiểm soát chi phí, khoảng $0.30/chỉ số/tháng):
`ColdStartDuration`, `ModelLoadDuration`, `InferenceDuration`, `TagsPerFile`

**Dashboard.** Một bảng điều khiển: số file xử lý được mỗi giờ, độ sâu hàng đợi, độ sâu DLQ, tỉ lệ lỗi, p50/p95/p99 thời gian xử lý, tỉ lệ khởi động nguội.

**Giữ log.** 14 ngày trên mọi log group, khai báo tường minh trong Terraform. Mặc định của CloudWatch là giữ vĩnh viễn, và đó là một khoản chi lặng lẽ tăng dần.

---

## 9. Bảo mật

| Mối lo | Biện pháp |
|---|---|
| Truy cập API | Cognito Authorizer trên mọi route, không có ngoại lệ |
| Truy cập của CI | OIDC với vai trò IAM tạm; **không lưu khoá truy cập AWS** |
| Truy cập bucket | Chặn truy cập công khai trên cả bốn bucket. Mọi lượt đọc đi qua URL đã ký có hạn |
| Quyền IAM | Một vai trò riêng cho mỗi Lambda, đúng quyền tối thiểu cần dùng. Không dùng vai trò dùng chung |
| Bí mật | Không có. Giá trị cấu hình nằm trong biến môi trường Lambda |
| Quét hạ tầng | `checkov` chạy trên mọi Pull Request |
| Tài khoản gốc | Bật MFA, không dùng để làm việc. Mọi thao tác qua một người dùng IAM riêng |

Bài tập gốc buộc mọi Lambda dùng chung một vai trò `LabRole` do ràng buộc của AWS Academy. Bản này dùng vai trò riêng cho từng Lambda — chính là điều người phỏng vấn muốn nghe, và cũng là nội dung một ADR.

---

## 10. Chiến lược kiểm thử

| Tầng | Kiểm cái gì | Chạy khi nào | Công cụ |
|---|---|---|---|
| Đơn vị | Tính vân tay, gộp nhãn, phân loại lỗi, tính kích thước ảnh nhỏ | Mọi commit | `pytest`, mô hình được giả lập |
| Hạ tầng | Cú pháp Terraform, định dạng, lỗ hổng bảo mật | Mọi PR | `terraform validate`, `tflint`, `checkov` |
| Tích hợp | Upload thật → chờ → nhãn đúng | Sau mỗi lần triển khai | `pytest` gọi API thật |
| Độ chính xác ML | 26 ảnh test so với đáp án | Khi đụng code ML | `ml/eval/run_eval.py` |

Test đơn vị **không** được phụ thuộc vào file `.pt` thật. Mô hình được giả lập, giống cách `ml/test_tagger.py` của bài gốc làm, để CI chạy trong vài giây chứ không phải vài phút.

---

## 11. Bố cục repo

```
wildlens/
├── README.md                    (tiếng Anh — cho nhà tuyển dụng)
├── docs/
│   ├── architecture.md
│   ├── adr/                     (tiếng Anh, 6 file)
│   ├── runbook.md
│   └── design/                  (tiếng Việt — tài liệu thiết kế)
├── infra/terraform/
│   ├── envs/dev/
│   ├── modules/{storage,auth,api,pipeline,notify,observability}/
│   └── gcp/
├── services/
│   ├── process/                 (Lambda container)
│   ├── api/{upload,search,status,delete,subscribe}/
│   └── gcp-resolve/
├── ml/
│   ├── tagger.py
│   ├── labels.txt
│   ├── eval/{run_eval.py,answer_key.yaml,reports/}
│   └── tests/
├── web/
└── .github/workflows/{ci.yml,deploy.yml,ml-eval.yml}
```

`infra/` và `services/` tách nhau vì chúng thay đổi với nhịp khác nhau; CI chỉ chạy phần bị ảnh hưởng.

**Không bao giờ commit:** `*.pt`, `*.pth`, `*.tfstate`, `*.tfvars` chứa giá trị thật, bất cứ thứ gì có khoá truy cập.

---

## 12. Kiểm soát chi phí

| Cơ chế | Chặn cái gì |
|---|---|
| Cảnh báo ngân sách $10 / $25 / $50 | Biết trước khi muộn |
| Đồng thời dành riêng = 2 | Vòng lặp lỗi không nhân bản được |
| Giữ log 14 ngày | Log không tích tụ vô hạn |
| Luật dọn ECR giữ 3 ảnh | Mỗi lần build là +4,5 GB |
| Luật dọn S3 xoá sau 30 ngày | Ảnh test không nằm lại mãi |
| Không dùng VPC | Tránh NAT Gateway $32/tháng |
| `terraform destroy` | Xoá sạch khi nghỉ dài |

**Chi phí dự kiến:** ~$0.75/tháng khi nghỉ, ~$3.30/tháng khi demo nhiều. $100 credit kéo được khoảng hai năm.

**Kịch bản xấu nhất có trần:** 2 Lambda × 4 GB × 900 s chạy liên tục 8 tiếng ≈ $4.

---

## 13. Lộ trình triển khai

Sắp xếp theo nguyên tắc: **cái gì gỡ lỗi khó thì làm sớm, và làm ở nơi dễ gỡ nhất.**

### Mốc A — AI chạy được trên mây (~6 buổi)

| GĐ | Nội dung |
|---|---|
| 0 | Cài công cụ, người dùng IAM + MFA, khởi tạo repo, **bật cảnh báo ngân sách ngay** |
| 1 | Terraform: các bucket S3 + DynamoDB. `apply` và `destroy` chạy sạch |
| 2 | ML chạy trên máy: `tagger.py`, 26 ảnh test, bộ chấm điểm, test đơn vị |
| 3 | Container + ECR + Lambda, **nạp mô hình từ S3**. Đo p95 khởi động nguội gốc |

Giai đoạn 3 cố tình dựng bản *chậm* trước. Không có con số "trước" thì không chứng minh được cải thiện ở giai đoạn 5.

### Mốc B — Demo được đầu-cuối (~7 buổi) — đủ để đưa vào CV

| GĐ | Nội dung |
|---|---|
| 4 | SQS + DLQ + sự kiện S3 + tính bất biến khi lặp + bốn trạng thái |
| 5 | **Tối ưu:** nhúng mô hình vào ảnh container, đo lại, ghi lại mức cải thiện |
| 6 | Cognito + API Gateway + các Lambda upload/search/status |
| 7 | Giao diện + cơ chế hỏi lại trạng thái |

### Mốc C — Trông như của kỹ sư thật (~3 buổi)

| GĐ | Nội dung |
|---|---|
| 8 | SNS: đăng ký nhận email theo loài |
| 9 | X-Ray, dashboard, cảnh báo, `runbook.md` |
| 10 | GitHub Actions: CI, triển khai, OIDC, checkov |

### Mốc D — Đầy đủ (~3,5 buổi)

| GĐ | Nội dung |
|---|---|
| 11 | GCP Cloud Run xác minh JWT chéo đám mây, dựng bằng cùng Terraform |
| 12 | Hỗ trợ video |
| 13 | README, 6 ADR, sơ đồ kiến trúc, video demo |

**Tổng: ~20 buổi (55–60 giờ).**

---

## 14. Nguyên tắc làm việc

Một giai đoạn chỉ được coi là xong khi trả lời được câu *"vì sao lại làm thế này?"* mà không cần nhìn lại ghi chú — không phải khi code vừa chạy được.

Lý do: một project trong CV mà không giải thích được khi phỏng vấn là một khoản nợ, không phải tài sản. Vì vậy mọi quyết định kiến trúc đều phải kèm lý do, và lý do đó được ghi thành ADR thay vì nằm trong trí nhớ.

---

## 15. Rủi ro đã biết

| Rủi ro | Giảm thiểu |
|---|---|
| Ảnh container 4,5 GB làm CI chậm và tốn dung lượng | Tầng dựng có bộ nhớ đệm; luật dọn ECR; chỉ build khi `services/process/**` đổi |
| Phần cứng Apple Silicon xây ảnh cho x86_64 | Luôn dùng `--platform linux/amd64`, khai báo trong Dockerfile |
| Mô hình nhúng vào ảnh khiến layer rất lớn | Chấp nhận; giới hạn Lambda cho ảnh container là 10 GB |
| GCP cần tài khoản riêng | Giai đoạn 11 nằm ở Mốc D; có thể bỏ mà project vẫn đứng vững |
| Quét toàn bảng không mở rộng được | Có chủ đích ở quy mô này; ADR ghi rõ điều kiện chuyển sang chỉ mục |
| Ước lượng 20 buổi có thể trượt | Mốc B là điểm dừng an toàn; C và D là phần cộng thêm |
