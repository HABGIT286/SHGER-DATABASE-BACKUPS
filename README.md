# مكتبة إدارة الأعمال — نسخة كاملة

مشروع مكتبة طلابية عربي RTL، يتضمن موقعًا، واجهة API، وبوت Telegram لإضافة المواد وإدارة الملفات.

## بنية المشروع

```text
business_library_full/
├── bot.py
├── index.html
├── requirements.txt
├── README.md
├── static/
│   ├── css/library.css
│   └── js/library.js
└── .github/workflows/
    ├── check.yml
    └── run-bot.yml
```

## الميزات

- واجهة عربية متجاوبة مع الهواتف والحاسوب، بطابع داكن/ذهبي.
- إحصاءات الملفات والإضافات اليومية والتحميلات والزيارات والإعجابات.
- بحث بالاسم والوصف، ترتيب حسب الأحدث والأقدم والاسم والإعجابات والتحميلات.
- رفوف ملفات، فهرس عام، أحدث إضافة، الملفات المثبتة.
- تفاصيل الملف، تنزيل، وإعجاب يمنع تكراره من نفس مفتاح المتصفح.
- رفع ملف من Telegram، إضافة وصف، مراجعة قبل النشر.
- إدارة بواسطة صاحب الحساب المحدد في `ADMIN_ID` ورمز `ADMIN_CODE`: استعراض الملفات، تعديل الوصف، تثبيت/إلغاء تثبيت، حذف مع تأكيد.
- API endpoints: `/api/files`, `/api/stats`, `/api/visit`, `/api/files/{id}/like`, `/api/files/{id}/download`, `/health`.

## التشغيل المحلي

1. Python 3.12+.
2. `pip install -r requirements.txt`
3. ضبط متغيرات البيئة (لا تضع التوكن في الملفات):
   - `TOKKEN`
   - `ADMIN_ID`
   - `ADMIN_CODE`
   - `GROUP_LINK` (اختياري)
   - `DB_PATH` (اختياري؛ الافتراضي `data/library.sqlite3` داخل المشروع)
4. `uvicorn bot:app --host 0.0.0.0 --port 8000`
5. فتح `http://127.0.0.1:8000`

## GitHub Actions

أضف Secrets في Settings → Secrets and variables → Actions:

- `TOKKEN`: توكن البوت
- `ADMIN_ID`: رقم Telegram ID الخاص بالمالك
- `ADMIN_CODE`: رمز الإدارة الخاص
- `GROUP_LINK`: رابط المجموعة (اختياري)

Workflow `Run Telegram Library Bot` يبدأ يدويًا من Actions → Run workflow، ويُجدول كل 6 ساعات. مدة كل تشغيل 350 دقيقة تقريبًا كحد أعلى، لذا GitHub Actions ليس استضافة 24/7.

**قيود الاستضافة:** GitHub-hosted runner مؤقت. عند انتهاء المهمة تتوقف الخدمة، وSQLite الموجودة على runner لا تُحفظ تلقائيًا. هذا الـ workflow مناسب للتجربة أو التشغيل المؤقت للبوت، وليس لاستضافة API عامة دائمة. الموقع يعرض بياناته عبر FastAPI، ولذلك لن تعمل الواجهة ببيانات حقيقية إذا لم تكن API متاحة للجمهور على استضافة مستمرة. لا ترفع secrets أو ملف قاعدة البيانات إلى مستودع عام.
