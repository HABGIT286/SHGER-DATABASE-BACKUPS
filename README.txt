BUSINESS LIBRARY — ملفات المشروع

المكونات
- index.html: واجهة المكتبة (HTML/CSS/JavaScript في ملف واحد).
- bot.py: بوت Telegram وخدمة API للواجهة.
- requirements.txt: حزم Python.
- railway.toml: إعداد تشغيل Railway.
- .github/workflows/check.yml: اختبار الكود والتحقق الاختياري من TOKKEN.

تشغيل دائم (Railway)
1. ارفع مجلد business_library إلى مستودع GitHub.
2. اربط المستودع بخدمة Railway (Deploy from GitHub).
3. أضف المتغيرات التالية في Railway Variables:
   TOKKEN = توكن BotFather
   ADMIN_ID = رقم Telegram ID الخاص بالمالك (ليس اسم المستخدم)
   ADMIN_CODE = رمز إدارة جديد تختاره (لا تعِد استخدام الرمز الذي كتبته بالمحادثة)
   DB_PATH = /data/library.sqlite3
   GROUP_LINK = https://t.me/+mZe3EZzQBCYxOTUy (اختياري)
4. أضف Railway Volume واربطه بمسار /data لحفظ قاعدة البيانات دائمًا.
5. تأكد من وجود domain عام للخدمة. الموقع والـ API يعملان من نفس العنوان.
6. ضع صورة الشعار باسم 1.jpg بجانب index.html داخل المشروع قبل النشر. إن لم توجد الصورة سيظهر شعار SVG بديل.

GitHub Actions Secret
Settings > Secrets and variables > Actions > New repository secret
Name: TOKKEN
Value: توكن البوت
الـ workflow يفحص الكود ويختبر التوكن عند push أو التشغيل اليدوي. هذا لا يضيف المتغير تلقائيًا إلى Railway. يجب إدخال TOKKEN أيضًا في Railway Variables لأن Railway هو الذي يشغل البوت دائمًا.

طريقة المالك
- من البوت، أرسل ADMIN_CODE بعد التأكد من حسابك يطابق ADMIN_ID.
- تظهر قائمة الملفات، أرسل رقم الملف، ثم اختر حذف أو تعديل الملاحظة.
- لا تشارك ADMIN_CODE أو TOKKEN.

الأمان وملاحظات النسخة الأولية
- لا تضع TOKKEN أو ADMIN_CODE داخل index.html أو ملفات Git.
- تحكم المالك يعتمد على ADMIN_ID وADMIN_CODE.
- قاعدة البيانات تحفظ معلومات الملف وTelegram file_id، والملف نفسه يبقى على Telegram.
- الملفات المنشورة عامة ويمكن تنزيلها.
- نسخة أولية؛ قبل الاستخدام العام يلزم تحسين التحقق من الإعجابات، وتحديد أنواع وأحجام الملفات، وإضافة قيود/معدل طلبات وحماية إضافية للـ API.
