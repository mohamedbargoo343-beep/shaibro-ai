# SHAIBRO AI Android v2

هذه النسخة تربط تطبيق Android بخادم SHAIBRO AI Cloud عند نشره على HTTPS.

## الجديد
- التطبيق يفحص اتصال الخادم.
- يرسل أوامر المحادثة إلى `/api/chat`.
- إذا تعذر الاتصال، يتحول تلقائياً للوضع المحلي.
- رابط الخادم موجود في مكان واحد داخل `app/build.gradle.kts`.
- لا توجد مفاتيح OpenAI أو Google داخل APK.

## قبل بناء APK
افتح:
`app/build.gradle.kts`

وابحث عن:
`https://YOUR-SHAIBRO-SERVER.example.com`

واستبدله برابط خادم SHAIBRO AI Cloud الحقيقي.

مثال:
`https://shaibro-ai.onrender.com`

## بناء APK
1. افتح المشروع في Android Studio.
2. Gradle Sync.
3. Build > Build App Bundles or APKs > Build APKs.
4. APK سيكون عادةً في:
`app/build/outputs/apk/debug/app-debug.apk`

## مهم
خادم v4 الحالي يستخدم تسجيل دخول Session Cookie.
للاستخدام الكامل داخل Android، يفضّل في المرحلة التالية إضافة API Token خاص بالتطبيق حتى لا يحتاج WebView إلى جلسة دخول يدوية.


## الجديد في Android v3
تمت إضافة API Token خاص بالتطبيق.

قبل بناء APK افتح:
`app/build.gradle.kts`

وغيّر:
- `API_BASE_URL` إلى رابط خادم SHAIBRO AI الحقيقي.
- `MOBILE_API_TOKEN` إلى نفس القيمة الموجودة في الخادم.

مثال:
`MOBILE_API_TOKEN=your-long-random-secret`

مهم: إذا كنت ستنشر المشروع كمستودع عام، لا تضع التوكن الحقيقي في Git.
