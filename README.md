# Davra — real vaqtli messenjer

FastAPI + WebSocket + SQLite/PostgreSQL asosidagi PWA messenjer: shaxsiy yozishmalar (so'rov asosida), guruhlar, kanallar, taklif tizimi, reaksiyalar, profil rasmlari, qidiruv.

## Lokal ishga tushirish

```powershell
pip install -r requirements.txt
python main.py            # yoki: python -m uvicorn main:app --reload
```

Brauzerda `http://localhost:8000`. `DATABASE_URL` berilmasa `./suhbat.db` (SQLite) ishlatiladi. PostgreSQL uchun `DATABASE_URL=postgresql://...`.

## Muhit o'zgaruvchilari

| Nomi | Vazifasi |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string (bo'lmasa SQLite) |
| `OWNER_USERNAME`, `OWNER_PASSWORD` (12+ belgi), `OWNER_DISPLAY_NAME` | Rasmiy egasi akkaunti (tasdiqlash belgisi) — birinchi ishga tushishda yaratiladi |
| `TRUSTED_PROXY_HOPS` | Serveringiz oldidagi proksilar soni. **Render: 1** (standart). Cloudflare ham bo'lsa 2. Login limiti shu orqali mijoz IP'sini to'g'ri aniqlaydi |
| `PYTHON_VERSION` | Render uchun `3.12.7` |

Haqiqiy parollarni repository'ga, README'ga yoki frontend kodiga yozmang. **`suhbat.db` ni GitHub'ga yuklamang** (`.gitignore` da bor).

## Funksiyalar

- **Shaxsiy chat:** begona odam avval so'rov yuboradi, qabul qilinguncha faqat bitta xabar yubora oladi; soatiga 20 tadan ko'p yangi chat ochib bo'lmaydi; rad etilgan foydalanuvchi yozolmaydi.
- **Guruhlar (100 tagacha) va kanallar:** a'zo qo'shish, nom o'zgartirish, a'zolar ro'yxati.
- **Takliflar:** o'zaro chati *qabul qilingan* (do'st) odam guruhga to'g'ridan-to'g'ri qo'shiladi. **Notanish odam taklif oladi** va o'zi qabul/rad etadi — guruh orqali spam qilib bo'lmaydi. Rad etgan odamga qayta taklif yuborilmaydi.
- **Rollar:** ega / admin / a'zo. Ega: admin tayinlash, egalikni topshirish, a'zoni chiqarish, suhbatni o'chirish. Admin: oddiy a'zoni chiqarish, nomni o'zgartirish, a'zo taklif qilish.
- **O'qilmaganlar:** shaxsiy chatlarda ham, guruh va kanallarda ham (sarlavhada `(N)` va filtr "O'qilmagan").
- **Eski xabarlar:** yuqoriga surilganda 100 tadan yuklanadi.
- **Xabarlar:** emoji reaksiyalari, tahrirlash/o'chirish, qidiruv, yozmoqda belgisi.
- **Profil:** rasm (JPEG/PNG/WebP, 2 MB gacha; EXIF burilishi to'g'rilanadi, 512×512 kvadrat), ism, bio, tungi rejim, bildirishnomalar.

## Yangi imkoniyatlar (v2)

- **Push bildirishnomalar** (ilova yopiq bo'lsa ham): Sozlamalar → Bildirishnomalar → yoqish. VAPID kalitlari birinchi ishga tushishda avtomatik yaratilib bazada (`app_config`) saqlanadi — Render'da qo'lda hech narsa sozlash shart emas (xohlasangiz `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` bilan almashtirish mumkin). iPhone'da avval ilovani «Bosh ekranga qo'shing».
- **Javob berish, yo'naltirish, qadash (pin), saqlash, @eslatma**, guruhda ✓✓ o'qildi belgisi va a'zo yorliqlari.
- **Rasm, fayl va ovozli xabar** (`MAX_UPLOAD_MB`, standart 8 MB; ovoz 3 daqiqagacha). Fayllar bazada saqlanadi, ishlatilmagan yuklamalar 24 soatdan keyin o'chiriladi.
- **Yon menyu va Sozlamalar** to'liq ishlaydi: profil, bildirishnomalar, maxfiylik (oxirgi ko'rilgan, notanish so'rovlari, parol, seanslar, bloklanganlar), suhbat sozlamalari (matn o'lchami, fon, Enter, 12 soat, tungi rejim), papkalar, qo'shimcha, til. Sozlamalar serverda saqlanadi va qurilmalar o'rtasida sinxronlanadi.
- «Davra Premium», «Nur» (valyuta) va «Sovg'alar» bo'limlari — hozircha **Tez kunda** (bosilmaydi).
- Frontend: `index.html`, `app.css`, `app.js`, `panels.js`, `sw.js`, `manifest.json`. Eski `features-ui.js` va `gen_vapid.py` endi kerak emas — o'chiring.

## Xavfsizlik

- Login: IP bo'yicha 10 daqiqada 20 urinish, noto'g'ri parol: (IP+username) bo'yicha 10 daqiqada 8 ta, username bo'yicha soatiga 60 ta.
- `X-Forwarded-For` ning mijoz yozgan qismiga ishonilmaydi (`TRUSTED_PROXY_HOPS` ga qarang).
- Parol kamida 8 belgi. Sessiya 90 kun amal qiladi.
- `owner`, `admin`, `support`, `davra` kabi nomlar va "Davra egasi" kabi ko'rinadigan ismlarni oddiy foydalanuvchi ola olmaydi.
- Matnlar `textContent` bilan chiqariladi (XSS yo'q); a'zolik har bir HTTP/WS amalida tekshiriladi.

## Profil rasmlari

Rasmlar **bazada** saqlanadi (`user_avatars` jadvali), shuning uchun Render'da persistent disk kerak emas. Eski `static/uploads/*.jpg` fayllari birinchi ishga tushishda avtomatik bazaga ko'chiriladi — shundan keyin `static/uploads/` papkasini o'chirib tashlash mumkin.

## Mavjud baza uchun migratsiya

Ishga tushganda `create_all` yangi jadvallarni (`room_invites`, `user_avatars`) yaratadi, alohida migratsiya yetishmayotgan ustunlarni (`room_members.last_read_id` va boshqalar) qo'shadi. Mavjud foydalanuvchi va xabarlar o'chirilmaydi, eski guruh xabarlari "o'qilgan" hisoblanadi. Baribir avval bazaning zaxira nusxasini oling.

## Deploy (Neon + Render)

1. Neon'da PostgreSQL baza yarating, connection stringni nusxalang.
2. Render Web Service: build `pip install -r requirements.txt`; start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. `DATABASE_URL`, `PYTHON_VERSION=3.12.7`, (ixtiyoriy) `OWNER_*` ni Environment'ga kiriting.
4. Health check: `https://SIZNING-APP.onrender.com/api/health`. Bepul Render 15 daqiqa harakatsizlikdan keyin uxlaydi (WebSocket uziladi) — UptimeRobot bilan shu manzilni 5 daqiqada bir ping qilib turing.

## Ma'lum cheklovlar

- Ilova yopiq bo'lganda push-bildirishnoma yo'q (Web Push hali qo'shilmagan); bildirishnoma faqat ochiq, fokussiz tabda ishlaydi.
- Limitlar va WebSocket holati bitta jarayon xotirasida — bitta worker bilan ishlating (Render bepul reja shunday).
- Xabarlar bazada oddiy matn sifatida saqlanadi (end-to-end shifrlash yo'q).
## Kanal/guruh boshqaruvi (Telegram uslubida)
- Adminlar alohida huquqlar bilan: ma'lumot, xabar yozish, o'chirish, qadash, a'zo qo'shish, chiqarish, admin tayinlash.
- Guruh/kanal: tahrirlash (nom, tavsif, rasm), imzo, reaksiyalar, ommaviy, taklif havolasi (`/?join=kod`), chiqarilganlar ro'yxati.
- Yangi ustunlar avtomatik qo'shiladi (migratsiya), eski adminlar standart huquq oladi.