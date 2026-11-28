# NOIR CONTROL — ربط حقيقي عبر Backend

هذه الحزمة ليست مجرد واجهة. الـ API يقرأ حالة GitHub Actions ويصدر أوامر تشغيل/إيقاف/إعادة تشغيل، ويقرأ المستخدمين وإحصاءات `library_index.json`. لا توجد أرقام تجريبية أو تقديرات CPU/RAM داخل اللوحة.

## ما الذي يعمل بعد نشر Backend وضبط متغيراته؟
- **Start:** يطلب dispatch للـ workflow.
- **Stop:** يطلب cancel لأحدث تشغيل نشط.
- **Restart:** يلغي التشغيل النشط ثم يرسل dispatch جديد.
- **Health / Status:** يقرأ آخر تشغيل من GitHub Actions.
- **Users & counts:** يقرأ `library_index.json` في الفرع المحدد.
- **Logs:** يعرض سجل تشغيل workflows الحقيقي (الحالة، رقم التشغيل، رابط GitHub). ليس console-log مباشر من البوت.
- **Admin login:** يتحقق من `WEB_ADMIN_PASSWORD` على الخادم ويستخدم جلسة Cookie؛ كلمة المرور لا تحفظ في HTML أو LocalStorage.


## متغيرات Backend (Render / hosting environment)

يجب إدخالها يدويًا في Environment Variables لخدمة Backend. لا يمكن لخدمة خارج GitHub أن تقرأ GitHub Actions Secrets تلقائيًا.


| المتغير | القيمة / الاستخدام |
|---|---|
| `GITHUB_REPOSITORY` | `OWNER/REPOSITORY` |
| `GH_TOKEN` | GitHub PAT fine-grained للمستودع نفسه؛ Actions Read & Write وContents Read |
| `WEB_ADMIN_PASSWORD` | كلمة مرور بوابة الإدارة (قوية، طويلة، مختلفة عن GitHub token) |
| `WORKFLOW_FILE` | اسم ملف workflow داخل `.github/workflows`، مطابق لاسم ملفك الفعلي |
| `WORKFLOW_REF` | اسم الفرع الذي يعمل عليه البوت، غالبًا `main` |
| `CORS_ORIGINS` | Origin موقع لوحة التحكم إذا كان مستضافًا في نطاق منفصل؛ لا تضع مسارًا أو `/` في النهاية |
| `COOKIE_SECURE` | `true` للنشر عبر HTTPS |


## أسرار GitHub Actions التي يحتاجها ملف تشغيل البوت

القيم تبقى في GitHub: **Settings → Secrets and variables → Actions**، ويقرأها workflow في وقت التشغيل:

- `TOKENN`
- `JOIN_CHANEL`
- `DEV_TELE`
- `ID_TELE`
- `PROXY_URL`
- `WELCOME_IMAGE`

`GH_TOKEN` المستخدم للوحة الإدارة يجب أن يُضاف أيضًا في Environment Variables لخدمة Backend (يمكن استخدام token منفصل ومحدود الصلاحيات). وجوده في GitHub Actions Secrets وحده لا يوصله إلى Backend.

`GROUP_LINK` و`WEB_ADMIN_PASSWORD` ليسا متغيرين يستخدمهما ملف البوت الحالي. `WEB_ADMIN_PASSWORD` يستخدمه هذا الـ Backend فقط. `GROUP_LINK` يحتاج إضافة دعم في كود البوت إذا أردت أن يؤثر على رسائل البوت.


## نشر سريع على Render

1. ارفع الحزمة إلى مستودعك بحيث يكون `render.yaml` في جذر المستودع، ومجلد `noir_backend` موجودًا كما هو.
2. في Render اختر **New → Blueprint** ثم المستودع.
3. أدخل القيم المطلوبة عند طلب Environment Variables.
4. تأكد من `WORKFLOW_FILE` و`WORKFLOW_REF` قبل النشر.
5. بعد النشر، افتح رابط خدمة Backend؛ ستظهر لوحة NOIR نفسها (الخادم يقدم `index.html` من نفس النطاق).
6. افتح أيقونة الإدارة وسجّل كلمة المرور. بعدها استخدم زر التحديث أو فحص الصحة.


## التشغيل المحلي

من داخل `noir_backend`:

```bash
pip install -r requirements.txt
GITHUB_REPOSITORY=OWNER/REPOSITORY GH_TOKEN=YOUR_GITHUB_TOKEN WEB_ADMIN_PASSWORD='YOUR_LONG_PASSWORD' WORKFLOW_FILE='ملف تشغيل v6.yml' WORKFLOW_REF=main COOKIE_SECURE=false uvicorn app:app --host 0.0.0.0 --port 8000
```

افتح `http://localhost:8000`. في التشغيل المحلي `COOKIE_SECURE=false` فقط، ولا تستخدمها في النشر HTTPS.

## حدود دقيقة

- حالة `in_progress` تعني أن GitHub Actions يعلن أن الـ workflow يعمل؛ لا تثبت أن Telegram polling سليم لحظيًا. لقياس صحة البوت نفسه بدقة نحتاج heartbeat من ملف البوت إلى API أو فحص Telegram.
- CPU/RAM لا تظهر لأن GitHub-hosted runner لا يرسل هذه القياسات إلى لوحة التحكم حاليًا.
- GitHub token لا يُعرض ولا يُرسل إلى المتصفح.
- جلسات الإدارة الحالية محفوظة في ذاكرة Backend؛ إعادة تشغيل خدمة Backend تُنهي الجلسات.
