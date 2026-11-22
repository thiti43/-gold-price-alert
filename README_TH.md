# Gold XAU/USD SMA20 — Cloud ฟรี (GitHub Actions)

**ข้อจำกัด:** ไม่ใช่ราคา GOLD NOW โดยตรง; XAUS เป็นราคาอ้างอิง และกราฟ SMA20 คำนวณจากข้อมูลรายวันของผู้ให้บริการ. เช็กทุก 5 นาทีแบบ best effort; GitHub อาจหน่วงหรือข้ามรอบ จึงไม่ใช่ Real-Time และอาจพลาดจุดตัดสั้น ๆ. ถ้าข้อมูลไม่สด ระบบจะไม่ส่งสัญญาณ.

## ตั้งค่า (ครั้งเดียว)
1. สมัคร GitHub ที่ https://github.com และสร้าง repository แบบ **Public** เพื่อใช้ standard GitHub Actions โดยไม่เสียค่า execution minutes (อย่าใส่ข้อมูลลับลงไฟล์โค้ด)
2. อัปโหลด **เนื้อหาภายในโฟลเดอร์นี้** ขึ้น repository รวมถึงโฟลเดอร์ซ่อน `.github/workflows/` โดยรักษาโครงสร้างไฟล์ (ง่ายที่สุดใช้ GitHub Desktop หรือ git push; การอัปโหลดไฟล์ผ่านหน้าเว็บต้องตรวจสอบว่าโฟลเดอร์ `.github` ไปครบ)
3. ติดตั้ง ntfy จาก App Store / Google Play แล้ว Subscribe หัวข้อสุ่มยาว ๆ ที่คุณสร้างเอง เช่น `gold-xau-` ตามด้วยตัวอักษร/ตัวเลขสุ่มอย่างน้อย 24 ตัว; อย่าใช้ชื่อทั่วไป เพราะ ntfy.sh เป็น public topic service
4. GitHub repo → Settings → Secrets and variables → Actions → New repository secret; Name: `NTFY_TOPIC`, Value: หัวข้อเดียวกับใน ntfy
5. GitHub repo → Actions → `XAUUSD SMA20 Cloud Alert` → Run workflow → เลือก `test_push=true` เพื่อทดสอบรับ Push บนโทรศัพท์
6. เมื่อทดสอบสำเร็จ ระบบจะทำงานอัตโนมัติประมาณทุก 5 นาที โดยไม่ต้องเปิดคอม

## ข้อควรรู้
- GitHub schedule อาจล่าช้าหรือบางรอบถูกข้าม; การตรวจ 5 นาทีไม่รับประกันจับ crossing ทุกครั้ง
- State ใช้ GitHub Actions cache ซึ่งอาจถูกลบ; หาก state หาย โปรแกรมจะเริ่มเก็บค่าใหม่ก่อน ไม่ยิงสัญญาณจากค่าเดา
- หากห่างจากรอบก่อนมากกว่า 10 นาที จะรีเซ็ต baseline เพื่อเลี่ยงสัญญาณหลอกจากข้อมูลที่ขาดช่วง
- โควตา GitHub Free สำหรับ private repositories มีจำกัด; เลือก public repository โดยเก็บ NTFY_TOPIC ใน Secrets
- ระบบนี้ไม่ใช่คำแนะนำซื้อขายทอง
