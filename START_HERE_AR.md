# SHAIBRO AI — حزمة التشغيل النهائية

هذه الحزمة تحتوي على:
1. خادم SHAIBRO AI Cloud v5.
2. تطبيق SHAIBRO AI Android v3.

## المطلوب لتشغيل التطبيق من أي مكان
### على الخادم
اضبط متغيرات البيئة:
- SESSION_SECRET
- ADMIN_PASSWORD
- MOBILE_API_TOKEN
- OPENAI_API_KEY (اختياري للذكاء الاصطناعي الحقيقي)
- GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI (لـ Gmail وCalendar)

### في Android
افتح:
`Android_App/app/build.gradle.kts`

ثم غيّر:
- API_BASE_URL إلى رابط الخادم HTTPS.
- MOBILE_API_TOKEN إلى نفس التوكن في الخادم.

بعدها ابنِ APK من Android Studio.

## النتيجة
عندما يكون الخادم منشوراً، تطبيق Android سيستخدم:
- المحادثة السحابية.
- المهام.
- Gmail وGoogle Calendar بعد ربط Google.
- الوضع المحلي إذا تعذر الاتصال.
