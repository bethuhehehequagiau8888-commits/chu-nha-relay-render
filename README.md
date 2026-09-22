# Chủ Nhà V3 Relay

Relay là cầu nối cho phone khác Wi-Fi, 4G hoặc 5G.

Luồng:

- Laptop Chủ Nhà đăng ký `stationId/stationKey`.
- Laptop đồng bộ danh sách phone và mission lên Relay.
- Phone Agent dùng `stationId/stationKey/deviceId` để hỏi Relay có việc không.
- Relay trả đúng job của phone đó.

V3 không mở thẳng laptop ra internet. Khi cần chạy thật, đặt thư mục `relay/` lên server/cloud/VPS rồi nhập Relay URL vào Tool Chủ Nhà.

Chạy thử local:

```bash
python3 run_relay.py
```

Relay local mặc định:

```text
http://127.0.0.1:8898
```
